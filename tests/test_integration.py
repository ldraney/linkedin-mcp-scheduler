"""Integration tests that hit the real LinkedIn API.

These tests create real posts on LinkedIn and delete them after.
They require valid credentials in the OS keychain.

Run with: uv run pytest tests/test_integration.py -v
Skipped automatically when credentials are missing.

NOTE: LinkedIn rate-limits post creation. Tests include delays to avoid 422s.
Run these tests individually or with patience, not in tight CI loops.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from linkedin_mcp_scheduler.db import ScheduledPostsDB
from linkedin_mcp_scheduler.token_storage import get_credentials


# ---------------------------------------------------------------------------
# Skip entire module if no credentials
# ---------------------------------------------------------------------------

_creds = get_credentials()
pytestmark = [
    pytest.mark.skipif(
        _creds is None,
        reason="No LinkedIn credentials found in keychain or env vars",
    ),
    pytest.mark.integration,
]

# LinkedIn rate-limits post creation. Pause between tests that hit the API.
RATE_LIMIT_DELAY = 3  # seconds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Real temp DB -- no singletons, no monkeypatching."""
    db_path = os.path.join(str(tmp_path), "integration.db")
    _db = ScheduledPostsDB(db_path)
    yield _db
    _db.close()


@pytest.fixture
def client():
    """Real LinkedInClient with real credentials."""
    from linkedin_sdk import LinkedInClient
    creds = get_credentials()
    return LinkedInClient(
        access_token=creds["accessToken"],
        person_id=creds["personId"],
    )


@pytest.fixture
def cleanup_posts(client):
    """Track post URNs created during test and delete them after."""
    urns = []
    yield urns
    for urn in urns:
        try:
            client.delete_post(urn)
        except Exception as e:
            print(f"Warning: failed to delete {urn}: {e}")


@pytest.fixture(autouse=True)
def _rate_limit_pause():
    """Pause between tests to avoid LinkedIn rate limiting."""
    yield
    time.sleep(RATE_LIMIT_DELAY)


# ---------------------------------------------------------------------------
# SDK smoke tests
# ---------------------------------------------------------------------------

class TestSDKSmoke:
    """Verify the SDK itself works against the real API."""

    def test_create_and_delete_text_post(self, client, cleanup_posts):
        result = client.create_post(
            commentary="[integration test] text post smoke test - will be deleted",
            visibility="PUBLIC",
        )
        urn = result.get("postUrn", result.get("id"))
        assert urn is not None, f"No post URN in response: {result}"
        cleanup_posts.append(urn)

    def test_create_and_delete_link_post(self, client, cleanup_posts):
        result = client.create_post_with_link(
            commentary="[integration test] link post smoke test - will be deleted",
            url="https://github.com/ldraney/linkedin-mcp-scheduler",
            visibility="PUBLIC",
        )
        urn = result.get("postUrn", result.get("id"))
        assert urn is not None, f"No post URN in response: {result}"
        cleanup_posts.append(urn)


# ---------------------------------------------------------------------------
# Daemon -> LinkedIn API (no mocks on the publish path)
# ---------------------------------------------------------------------------

class TestDaemonPublishIntegration:
    """DB.add() -> daemon.run_once() -> real LinkedIn API -> DB.mark_published()"""

    def test_daemon_publishes_text_post(self, db, client, cleanup_posts):
        from unittest.mock import patch
        from linkedin_mcp_scheduler.daemon import run_once

        post = db.add(
            commentary="[integration test] daemon text publish - will be deleted",
            scheduled_time="2000-01-01T00:00:00+00:00",
            visibility="PUBLIC",
        )

        with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=db), \
             patch("linkedin_mcp_scheduler.daemon._get_client", return_value=client):
            run_once()

        updated = db.get(post["id"])
        assert updated["status"] == "published", f"Expected published, got: {updated}"
        assert updated["post_urn"] is not None
        assert updated["published_at"] is not None
        cleanup_posts.append(updated["post_urn"])

    def test_daemon_publishes_link_post(self, db, client, cleanup_posts):
        from unittest.mock import patch
        from linkedin_mcp_scheduler.daemon import run_once

        post = db.add(
            commentary="[integration test] daemon link publish - will be deleted",
            scheduled_time="2000-01-01T00:00:00+00:00",
            url="https://github.com/ldraney/linkedin-mcp-scheduler",
            visibility="PUBLIC",
        )

        with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=db), \
             patch("linkedin_mcp_scheduler.daemon._get_client", return_value=client):
            run_once()

        updated = db.get(post["id"])
        assert updated["status"] == "published"
        assert updated["post_urn"] is not None
        cleanup_posts.append(updated["post_urn"])

    def test_daemon_skips_future_posts(self, db, client):
        """Future posts should not be published -- no API call made."""
        from unittest.mock import patch
        from linkedin_mcp_scheduler.daemon import run_once

        db.add(
            commentary="[integration test] should NOT be published",
            scheduled_time="2099-01-01T00:00:00+00:00",
            visibility="PUBLIC",
        )

        with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=db), \
             patch("linkedin_mcp_scheduler.daemon._get_client", return_value=client):
            run_once()

        posts = db.list()
        assert all(p["status"] == "pending" for p in posts)

    def test_daemon_marks_failed_on_bad_credentials(self, db):
        """Real SDK with invalid token -> 401 -> daemon marks failed, doesn't crash."""
        from unittest.mock import patch
        from linkedin_sdk import LinkedInClient
        from linkedin_mcp_scheduler.daemon import run_once

        bad_client = LinkedInClient(access_token="invalid_token", person_id="invalid")

        post = db.add(
            commentary="[integration test] bad creds - should fail",
            scheduled_time="2000-01-01T00:00:00+00:00",
        )

        with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=db), \
             patch("linkedin_mcp_scheduler.daemon._get_client", return_value=bad_client):
            run_once()  # Should not raise

        updated = db.get(post["id"])
        assert updated["status"] == "failed"
        assert updated["error_message"] is not None
        assert updated["retry_count"] == 1


# ---------------------------------------------------------------------------
# Full stack: MCP protocol -> DB -> daemon -> LinkedIn API
# ---------------------------------------------------------------------------

class TestFullStackIntegration:
    """MCP client -> stdio -> server -> DB -> daemon -> real LinkedIn API."""

    def test_schedule_via_mcp_then_publish_via_daemon(self, client, cleanup_posts, tmp_path):
        """
        The real thing, zero mocks on the publish path:
        1. Schedule post through MCP stdio protocol
        2. Backdate via DB (tool correctly rejects past times)
        3. Daemon publishes via real LinkedIn API
        4. Verify post URN in DB
        5. Cleanup deletes from LinkedIn
        """
        import asyncio
        import sqlite3
        from unittest.mock import patch
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        db_path = os.path.join(str(tmp_path), "mcp_integration.db")

        async def schedule_via_mcp():
            params = StdioServerParameters(
                command="uv",
                args=["run", "--directory", "/Users/ldraney/linkedin-mcp-scheduler", "linkedin-mcp-scheduler"],
                env={"DB_PATH": db_path},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    r = await session.call_tool("schedule_post", {
                        "commentary": "[integration test] full stack MCP -> daemon -> LinkedIn - will be deleted",
                        "scheduled_time": "2099-01-01T00:00:00+00:00",
                    })
                    data = json.loads(r.content[0].text)
                    assert "postId" in data, f"schedule_post failed: {data}"
                    return data["postId"]

        post_id = asyncio.run(schedule_via_mcp())

        # Backdate so daemon considers it due
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE scheduled_posts SET scheduled_time = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", post_id),
        )
        conn.commit()
        conn.close()

        # Daemon publishes with real LinkedIn client
        from linkedin_mcp_scheduler.daemon import run_once

        integration_db = ScheduledPostsDB(db_path)
        try:
            with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=integration_db), \
                 patch("linkedin_mcp_scheduler.daemon._get_client", return_value=client):
                run_once()

            updated = integration_db.get(post_id)
            assert updated["status"] == "published", f"Expected published, got: {updated}"
            assert updated["post_urn"] is not None
            cleanup_posts.append(updated["post_urn"])
        finally:
            integration_db.close()
