"""Tests for the publisher daemon run_once() function.

The LinkedInClient mock is justified — we can't call real LinkedIn in tests.
Everything else (DB, state transitions, dispatch logic) is real.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

import pytest

from linkedin_mcp_scheduler.db import ScheduledPostsDB, reset_db


PAST_TIME = "2000-01-01T00:00:00+00:00"
PAST_TIME_2 = "2000-01-02T00:00:00+00:00"
FUTURE_TIME = "2099-12-31T23:59:59+00:00"


@pytest.fixture
def db(tmp_path):
    """Create a fresh DB for each test and wire it into the daemon module."""
    db_path = os.path.join(str(tmp_path), "test.db")
    _db = ScheduledPostsDB(db_path)
    with patch("linkedin_mcp_scheduler.daemon.get_db", return_value=_db):
        yield _db
    _db.close()


@pytest.fixture
def mock_client():
    """Return a mocked LinkedInClient wired into the daemon."""
    client = MagicMock()
    client.create_post.return_value = {"postUrn": "urn:li:share:111"}
    client.create_post_with_link.return_value = {"postUrn": "urn:li:share:222"}
    with patch("linkedin_mcp_scheduler.daemon._build_client", return_value=client):
        yield client


class TestRunOnceNoUrl:
    """Posts without a URL should use create_post()."""

    def test_calls_create_post(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        db.add("Hello world", PAST_TIME)
        run_once()

        mock_client.create_post.assert_called_once_with(
            commentary="Hello world",
            visibility="PUBLIC",
        )
        mock_client.create_post_with_link.assert_not_called()


class TestRunOnceWithUrl:
    """Posts with a URL should use create_post_with_link()."""

    def test_calls_create_post_with_link(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        db.add("Check this out", PAST_TIME, url="https://example.com")
        run_once()

        mock_client.create_post_with_link.assert_called_once_with(
            commentary="Check this out",
            url="https://example.com",
            visibility="PUBLIC",
        )
        mock_client.create_post.assert_not_called()


class TestRunOnceMarksPublished:
    """Successful publish should mark the post as published."""

    def test_marks_published_on_success(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        post = db.add("Publish me", PAST_TIME)
        run_once()

        updated = db.get(post["id"])
        assert updated["status"] == "published"
        assert updated["post_urn"] == "urn:li:share:111"


class TestRunOnceMarksFailed:
    """Exception during publish should mark the post as failed."""

    def test_marks_failed_on_exception(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        mock_client.create_post.side_effect = RuntimeError("API exploded")

        post = db.add("Will fail", PAST_TIME)
        run_once()

        updated = db.get(post["id"])
        assert updated["status"] == "failed"
        assert "API exploded" in updated["error_message"]

    def test_marks_failed_increments_retry_count(self, db, mock_client):
        """retry_count should be 1 after first failure through daemon."""
        from linkedin_mcp_scheduler.daemon import run_once

        mock_client.create_post.side_effect = RuntimeError("oops")
        post = db.add("Will fail", PAST_TIME)
        run_once()

        updated = db.get(post["id"])
        assert updated["retry_count"] == 1


class TestRunOnceNoDuePosts:
    """run_once() should do nothing when there are no due posts."""

    def test_does_nothing_when_no_posts_due(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        # Only a future post exists — not due yet
        db.add("Future post", FUTURE_TIME)
        run_once()

        mock_client.create_post.assert_not_called()
        mock_client.create_post_with_link.assert_not_called()

    def test_does_nothing_with_empty_db(self, db, mock_client):
        """No posts at all — should not even create a client."""
        from linkedin_mcp_scheduler.daemon import run_once

        run_once()

        mock_client.create_post.assert_not_called()
        mock_client.create_post_with_link.assert_not_called()


class TestRunOnceMultiplePosts:
    """run_once() should process all due posts, not just the first."""

    def test_publishes_multiple_due_posts(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        p1 = db.add("Post one", PAST_TIME)
        p2 = db.add("Post two", PAST_TIME_2)
        run_once()

        assert mock_client.create_post.call_count == 2
        assert db.get(p1["id"])["status"] == "published"
        assert db.get(p2["id"])["status"] == "published"

    def test_mixed_success_and_failure(self, db, mock_client):
        """First post succeeds, second post fails. Both should be processed."""
        from linkedin_mcp_scheduler.daemon import run_once

        p1 = db.add("Will succeed", PAST_TIME)
        p2 = db.add("Will fail", PAST_TIME_2)

        # First call succeeds, second raises
        mock_client.create_post.side_effect = [
            {"postUrn": "urn:li:share:111"},
            RuntimeError("API down"),
        ]
        run_once()

        assert db.get(p1["id"])["status"] == "published"
        assert db.get(p2["id"])["status"] == "failed"
        assert "API down" in db.get(p2["id"])["error_message"]

    def test_mixed_url_and_no_url(self, db, mock_client):
        """Posts with and without URLs dispatched to correct SDK methods."""
        from linkedin_mcp_scheduler.daemon import run_once

        db.add("No URL", PAST_TIME)
        db.add("With URL", PAST_TIME_2, url="https://example.com")
        run_once()

        mock_client.create_post.assert_called_once()
        mock_client.create_post_with_link.assert_called_once()


class TestRunOncePostUrnFallback:
    """The daemon should handle various response shapes for post URN."""

    def test_uses_postUrn_field(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        mock_client.create_post.return_value = {"postUrn": "urn:li:share:999"}
        post = db.add("Test", PAST_TIME)
        run_once()

        assert db.get(post["id"])["post_urn"] == "urn:li:share:999"

    def test_falls_back_to_id_field(self, db, mock_client):
        """Some API responses use 'id' instead of 'postUrn'."""
        from linkedin_mcp_scheduler.daemon import run_once

        mock_client.create_post.return_value = {"id": "urn:li:share:alt"}
        post = db.add("Test", PAST_TIME)
        run_once()

        assert db.get(post["id"])["post_urn"] == "urn:li:share:alt"

    def test_falls_back_to_unknown(self, db, mock_client):
        """If response has neither postUrn nor id, store 'unknown'."""
        from linkedin_mcp_scheduler.daemon import run_once

        mock_client.create_post.return_value = {"something_else": "value"}
        post = db.add("Test", PAST_TIME)
        run_once()

        assert db.get(post["id"])["post_urn"] == "unknown"
        assert db.get(post["id"])["status"] == "published"


class TestRunOnceVisibility:
    """Visibility should be passed through to the SDK."""

    def test_passes_custom_visibility(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        db.add("Private post", PAST_TIME, visibility="CONNECTIONS")
        run_once()

        mock_client.create_post.assert_called_once_with(
            commentary="Private post",
            visibility="CONNECTIONS",
        )
