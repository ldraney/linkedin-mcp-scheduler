"""Integration tests that hit the real LinkedIn API.

These tests create real posts on LinkedIn and delete them after.
They require valid credentials in the OS keychain.

Run with: uv run pytest -m integration -v
Skipped automatically when credentials are missing.

NOTE: LinkedIn rate-limits post creation. Tests include delays to avoid 422s.
"""

from __future__ import annotations

import json
import os
import subprocess
import sqlite3
import time
from datetime import datetime, timedelta, timezone

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

RATE_LIMIT_DELAY = 3  # seconds between tests that create posts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Real temp DB."""
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
             patch("linkedin_mcp_scheduler.daemon._build_client", return_value=client):
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
             patch("linkedin_mcp_scheduler.daemon._build_client", return_value=client):
            run_once()

        updated = db.get(post["id"])
        assert updated["status"] == "published"
        assert updated["post_urn"] is not None
        cleanup_posts.append(updated["post_urn"])

    def test_daemon_skips_future_posts(self, db, client):
        from unittest.mock import patch
        from linkedin_mcp_scheduler.daemon import run_once

        db.add(
            commentary="[integration test] should NOT be published",
            scheduled_time="2099-01-01T00:00:00+00:00",
            visibility="PUBLIC",
        )

        with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=db), \
             patch("linkedin_mcp_scheduler.daemon._build_client", return_value=client):
            run_once()

        posts = db.list()
        assert all(p["status"] == "pending" for p in posts)

    def test_daemon_marks_failed_on_bad_credentials(self, db):
        from unittest.mock import patch
        from linkedin_sdk import LinkedInClient
        from linkedin_mcp_scheduler.daemon import run_once

        bad_client = LinkedInClient(access_token="invalid_token", person_id="invalid")

        post = db.add(
            commentary="[integration test] bad creds - should fail",
            scheduled_time="2000-01-01T00:00:00+00:00",
        )

        with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=db), \
             patch("linkedin_mcp_scheduler.daemon._build_client", return_value=bad_client):
            run_once()

        updated = db.get(post["id"])
        assert updated["status"] == "failed"
        assert updated["error_message"] is not None
        assert updated["retry_count"] == 1


# ---------------------------------------------------------------------------
# End-to-end: all 8 tools + daemon as real subprocess + real LinkedIn API
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """
    The real QA test. All 8 MCP tools exercised in one connected flow.
    Daemon runs as an actual subprocess with a real poll loop.
    Posts scheduled for near-future, daemon publishes them on time.
    Everything deleted from LinkedIn after.
    """

    def test_full_lifecycle_all_8_tools(self, client, cleanup_posts, tmp_path):
        db_path = os.path.join(str(tmp_path), "e2e.db")
        project_dir = "/Users/ldraney/linkedin-mcp-scheduler"
        now = datetime.now(timezone.utc)

        # We'll schedule posts 60s and 90s from now.
        # Daemon polls every 5s so it should catch them quickly.
        t1 = (now + timedelta(seconds=60)).isoformat()
        t2 = (now + timedelta(seconds=90)).isoformat()
        t3 = (now + timedelta(seconds=120)).isoformat()  # will be cancelled

        import asyncio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def exercise_tools():
            params = StdioServerParameters(
                command="uv",
                args=["run", "--directory", project_dir, "linkedin-mcp-scheduler"],
                env={"DB_PATH": db_path},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # --- 1. queue_summary: empty ---
                    r = await session.call_tool("queue_summary", {})
                    data = json.loads(r.content[0].text)
                    assert data["counts"] == {}, f"Expected empty queue: {data}"
                    print("PASS 1: queue_summary empty")

                    # --- 2. schedule_post: 3 posts ---
                    ids = []
                    for i, (t, note) in enumerate([
                        (t1, "E2E post 1 - text"),
                        (t2, "E2E post 2 - will be edited"),
                        (t3, "E2E post 3 - will be cancelled"),
                    ]):
                        r = await session.call_tool("schedule_post", {
                            "commentary": f"[e2e test] {note} - will be deleted",
                            "scheduled_time": t,
                        })
                        d = json.loads(r.content[0].text)
                        assert "postId" in d, f"schedule_post failed: {d}"
                        assert d["status"] == "pending"
                        ids.append(d["postId"])
                        time.sleep(0.5)  # small delay between schedules
                    print(f"PASS 2: scheduled 3 posts: {ids}")

                    # --- 3. list_scheduled_posts ---
                    r = await session.call_tool("list_scheduled_posts", {})
                    data = json.loads(r.content[0].text)
                    assert data["count"] == 3, f"Expected 3 posts: {data}"
                    print("PASS 3: list_scheduled_posts count=3")

                    # --- 4. get_scheduled_post ---
                    for pid in ids:
                        r = await session.call_tool("get_scheduled_post", {"post_id": pid})
                        data = json.loads(r.content[0].text)
                        assert "post" in data, f"get failed: {data}"
                        assert data["post"]["id"] == pid
                    print("PASS 4: get_scheduled_post all 3")

                    # --- 5. update_scheduled_post: edit post 2 ---
                    r = await session.call_tool("update_scheduled_post", {
                        "post_id": ids[1],
                        "commentary": "[e2e test] E2E post 2 - EDITED - will be deleted",
                    })
                    data = json.loads(r.content[0].text)
                    assert data["success"] is True
                    assert "EDITED" in data["post"]["commentary"]
                    print("PASS 5: update_scheduled_post")

                    # --- 6. reschedule_post: push post 3 further ---
                    far_future = (now + timedelta(hours=1)).isoformat()
                    r = await session.call_tool("reschedule_post", {
                        "post_id": ids[2],
                        "scheduled_time": far_future,
                    })
                    data = json.loads(r.content[0].text)
                    assert data["success"] is True
                    print("PASS 6: reschedule_post")

                    # --- 7. cancel_scheduled_post: cancel post 3 ---
                    r = await session.call_tool("cancel_scheduled_post", {
                        "post_id": ids[2],
                    })
                    data = json.loads(r.content[0].text)
                    assert data["status"] == "cancelled"
                    print("PASS 7: cancel_scheduled_post")

                    # --- 8. queue_summary: 2 pending, 1 cancelled ---
                    r = await session.call_tool("queue_summary", {})
                    data = json.loads(r.content[0].text)
                    assert data["counts"].get("pending") == 2, f"Expected 2 pending: {data}"
                    assert data["counts"].get("cancelled") == 1, f"Expected 1 cancelled: {data}"
                    print("PASS 8: queue_summary 2 pending + 1 cancelled")

                    return ids

        ids = asyncio.run(exercise_tools())
        print(f"\nAll 8 tools passed. Post IDs: {ids}")
        print(f"Posts 1 and 2 scheduled for ~{t1} and ~{t2}")
        print(f"Post 3 cancelled. Now starting daemon...\n")

        # --- 9. Start daemon as subprocess, wait for posts to publish ---
        daemon = subprocess.Popen(
            ["uv", "run", "--directory", project_dir, "linkedin-mcp-scheduler-daemon"],
            env={**os.environ, "DB_PATH": db_path, "POLL_INTERVAL_SECONDS": "5"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            # Wait for both posts to be published (timeout: 3 min past now)
            deadline = time.time() + 180
            e2e_db = ScheduledPostsDB(db_path)

            while time.time() < deadline:
                p1 = e2e_db.get(ids[0])
                p2 = e2e_db.get(ids[1])
                p1_done = p1 and p1["status"] in ("published", "failed")
                p2_done = p2 and p2["status"] in ("published", "failed")
                if p1_done and p2_done:
                    break
                time.sleep(5)

            # Verify both published
            p1 = e2e_db.get(ids[0])
            p2 = e2e_db.get(ids[1])
            assert p1["status"] == "published", f"Post 1 not published: {p1}"
            assert p2["status"] == "published", f"Post 2 not published: {p2}"
            assert p1["post_urn"] is not None
            assert p2["post_urn"] is not None
            cleanup_posts.append(p1["post_urn"])
            cleanup_posts.append(p2["post_urn"])
            print(f"PASS 9: daemon published post 1 -> {p1['post_urn']}")
            print(f"PASS 9: daemon published post 2 -> {p2['post_urn']}")

            # Verify post 3 still cancelled (not published)
            p3 = e2e_db.get(ids[2])
            assert p3["status"] == "cancelled", f"Post 3 should be cancelled: {p3}"
            print("PASS 9: post 3 still cancelled (correct)")

            # --- 10. Force-fail a new post, retry it, let daemon publish ---
            time.sleep(RATE_LIMIT_DELAY)
            retry_post = e2e_db.add(
                commentary="[e2e test] retry test - will be deleted",
                scheduled_time="2000-01-01T00:00:00+00:00",
            )
            e2e_db.mark_failed(retry_post["id"], "Simulated failure for E2E test")

            # Use MCP tool to retry it with a time that's already past (via direct DB)
            retry_time = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()

            async def retry_via_mcp():
                params = StdioServerParameters(
                    command="uv",
                    args=["run", "--directory", project_dir, "linkedin-mcp-scheduler"],
                    env={"DB_PATH": db_path},
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        r = await session.call_tool("retry_failed_post", {
                            "post_id": retry_post["id"],
                            "scheduled_time": retry_time,
                        })
                        data = json.loads(r.content[0].text)
                        assert data["success"] is True
                        assert data["post"]["status"] == "pending"
                        return data

            asyncio.run(retry_via_mcp())
            print("PASS 10: retry_failed_post reset to pending")

            # Wait for daemon to publish the retried post
            retry_deadline = time.time() + 60
            while time.time() < retry_deadline:
                rp = e2e_db.get(retry_post["id"])
                if rp and rp["status"] in ("published", "failed"):
                    break
                time.sleep(5)

            rp = e2e_db.get(retry_post["id"])
            assert rp["status"] == "published", f"Retried post not published: {rp}"
            cleanup_posts.append(rp["post_urn"])
            print(f"PASS 10: daemon published retried post -> {rp['post_urn']}")

            # --- 11. Final queue_summary ---
            async def final_summary():
                params = StdioServerParameters(
                    command="uv",
                    args=["run", "--directory", project_dir, "linkedin-mcp-scheduler"],
                    env={"DB_PATH": db_path},
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        r = await session.call_tool("queue_summary", {})
                        return json.loads(r.content[0].text)

            summary = asyncio.run(final_summary())
            assert summary["counts"].get("published") == 3, f"Expected 3 published: {summary}"
            assert summary["counts"].get("cancelled") == 1, f"Expected 1 cancelled: {summary}"
            assert summary["counts"].get("pending", 0) == 0, f"Expected 0 pending: {summary}"
            print(f"PASS 11: final queue_summary: {summary['counts']}")

            e2e_db.close()

        finally:
            # Stop daemon
            daemon.terminate()
            daemon.wait(timeout=10)
            stdout = daemon.stdout.read()
            print(f"\n--- Daemon output ---\n{stdout}")
            print(f"Cleaning up {len(cleanup_posts)} posts from LinkedIn...")
