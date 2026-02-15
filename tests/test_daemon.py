"""Tests for the publisher daemon run_once() function."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from linkedin_mcp_scheduler.db import ScheduledPostsDB, reset_db


PAST_TIME = "2000-01-01T00:00:00+00:00"
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
    with patch("linkedin_mcp_scheduler.daemon._get_client", return_value=client):
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


class TestRunOnceNoDuePosts:
    """run_once() should do nothing when there are no due posts."""

    def test_does_nothing_when_no_posts_due(self, db, mock_client):
        from linkedin_mcp_scheduler.daemon import run_once

        # Only a future post exists — not due yet
        db.add("Future post", FUTURE_TIME)
        run_once()

        mock_client.create_post.assert_not_called()
        mock_client.create_post_with_link.assert_not_called()
