"""Tests for the ScheduledPostsDB class."""

from __future__ import annotations

import os
import tempfile

import pytest

from linkedin_mcp_scheduler.db import ScheduledPostsDB


@pytest.fixture
def db(tmp_path):
    """Create a ScheduledPostsDB backed by a temp file."""
    db_path = os.path.join(str(tmp_path), "test.db")
    return ScheduledPostsDB(db_path)


FUTURE_TIME = "2099-12-31T23:59:59+00:00"
PAST_TIME = "2000-01-01T00:00:00+00:00"


class TestAdd:
    def test_add_returns_post_with_id(self, db):
        post = db.add("Hello LinkedIn!", FUTURE_TIME)
        assert post["id"]
        assert post["commentary"] == "Hello LinkedIn!"
        assert post["scheduled_time"] == FUTURE_TIME
        assert post["status"] == "pending"
        assert post["visibility"] == "PUBLIC"
        assert post["retry_count"] == 0

    def test_add_with_url_and_visibility(self, db):
        post = db.add("Check this out", FUTURE_TIME, url="https://example.com", visibility="CONNECTIONS")
        assert post["url"] == "https://example.com"
        assert post["visibility"] == "CONNECTIONS"


class TestGet:
    def test_get_existing(self, db):
        added = db.add("Test post", FUTURE_TIME)
        fetched = db.get(added["id"])
        assert fetched is not None
        assert fetched["id"] == added["id"]
        assert fetched["commentary"] == "Test post"

    def test_get_nonexistent(self, db):
        assert db.get("nonexistent-uuid") is None


class TestList:
    def test_list_all(self, db):
        db.add("Post 1", FUTURE_TIME)
        db.add("Post 2", FUTURE_TIME)
        posts = db.list()
        assert len(posts) == 2

    def test_list_with_status_filter(self, db):
        p1 = db.add("Post 1", FUTURE_TIME)
        db.add("Post 2", FUTURE_TIME)
        db.cancel(p1["id"])

        pending = db.list(status="pending")
        assert len(pending) == 1
        assert pending[0]["commentary"] == "Post 2"

        cancelled = db.list(status="cancelled")
        assert len(cancelled) == 1
        assert cancelled[0]["commentary"] == "Post 1"

    def test_list_respects_limit(self, db):
        for i in range(5):
            db.add(f"Post {i}", FUTURE_TIME)
        posts = db.list(limit=3)
        assert len(posts) == 3


class TestCancel:
    def test_cancel_pending(self, db):
        post = db.add("Cancel me", FUTURE_TIME)
        result = db.cancel(post["id"])
        assert result is not None
        assert result["status"] == "cancelled"

    def test_cancel_nonexistent(self, db):
        assert db.cancel("nonexistent") is None

    def test_cancel_non_pending(self, db):
        post = db.add("Already done", PAST_TIME)
        db.mark_published(post["id"], "urn:li:share:123")
        assert db.cancel(post["id"]) is None


class TestUpdate:
    def test_update_commentary(self, db):
        post = db.add("Original text", FUTURE_TIME)
        result = db.update(post["id"], commentary="Updated text")
        assert result is not None
        assert result["commentary"] == "Updated text"
        assert result["visibility"] == "PUBLIC"  # unchanged

    def test_update_url(self, db):
        post = db.add("Post", FUTURE_TIME)
        result = db.update(post["id"], url="https://new-url.com")
        assert result["url"] == "https://new-url.com"

    def test_update_visibility(self, db):
        post = db.add("Post", FUTURE_TIME)
        result = db.update(post["id"], visibility="CONNECTIONS")
        assert result["visibility"] == "CONNECTIONS"

    def test_update_multiple_fields(self, db):
        post = db.add("Post", FUTURE_TIME)
        result = db.update(post["id"], commentary="New text", url="https://x.com")
        assert result["commentary"] == "New text"
        assert result["url"] == "https://x.com"

    def test_update_non_pending_fails(self, db):
        post = db.add("Post", PAST_TIME)
        db.mark_published(post["id"], "urn:li:share:123")
        assert db.update(post["id"], commentary="Nope") is None

    def test_update_nonexistent_fails(self, db):
        assert db.update("nonexistent", commentary="Nope") is None

    def test_update_no_fields_returns_unchanged(self, db):
        post = db.add("Original", FUTURE_TIME)
        result = db.update(post["id"])
        assert result is not None
        assert result["commentary"] == "Original"


class TestReschedule:
    def test_reschedule_pending(self, db):
        post = db.add("Post", FUTURE_TIME)
        new_time = "2099-06-15T10:00:00+00:00"
        result = db.reschedule(post["id"], new_time)
        assert result is not None
        assert result["scheduled_time"] == new_time

    def test_reschedule_non_pending_fails(self, db):
        post = db.add("Post", PAST_TIME)
        db.mark_published(post["id"], "urn:li:share:123")
        assert db.reschedule(post["id"], FUTURE_TIME) is None


class TestGetDue:
    def test_get_due_returns_past_pending(self, db):
        db.add("Due post", PAST_TIME)
        db.add("Not due", FUTURE_TIME)
        due = db.get_due()
        assert len(due) == 1
        assert due[0]["commentary"] == "Due post"

    def test_get_due_excludes_non_pending(self, db):
        post = db.add("Past post", PAST_TIME)
        db.mark_published(post["id"], "urn:li:share:123")
        assert len(db.get_due()) == 0


class TestMarkPublished:
    def test_mark_published(self, db):
        post = db.add("Post", PAST_TIME)
        result = db.mark_published(post["id"], "urn:li:share:456")
        assert result["status"] == "published"
        assert result["post_urn"] == "urn:li:share:456"
        assert result["published_at"] is not None


class TestMarkFailed:
    def test_mark_failed(self, db):
        post = db.add("Post", PAST_TIME)
        result = db.mark_failed(post["id"], "API error 500")
        assert result["status"] == "failed"
        assert result["error_message"] == "API error 500"
        assert result["retry_count"] == 1

    def test_mark_failed_increments_retry(self, db):
        post = db.add("Post", PAST_TIME)
        db.mark_failed(post["id"], "Error 1")
        result = db.mark_failed(post["id"], "Error 2")
        assert result["retry_count"] == 2


class TestRetry:
    def test_retry_failed_post(self, db):
        post = db.add("Post", PAST_TIME)
        db.mark_failed(post["id"], "API error")
        result = db.retry(post["id"])
        assert result is not None
        assert result["status"] == "pending"
        assert result["error_message"] is None

    def test_retry_with_custom_time(self, db):
        post = db.add("Post", PAST_TIME)
        db.mark_failed(post["id"], "API error")
        result = db.retry(post["id"], scheduled_time=FUTURE_TIME)
        assert result["scheduled_time"] == FUTURE_TIME
        assert result["status"] == "pending"

    def test_retry_non_failed_returns_none(self, db):
        post = db.add("Post", FUTURE_TIME)
        assert db.retry(post["id"]) is None


class TestSummary:
    def test_empty_summary(self, db):
        s = db.summary()
        assert s["counts"] == {}
        assert s["next_due"] is None
        assert s["recent_failure"] is None

    def test_summary_with_data(self, db):
        db.add("Pending 1", FUTURE_TIME)
        db.add("Pending 2", FUTURE_TIME)
        p3 = db.add("Will fail", PAST_TIME)
        db.mark_failed(p3["id"], "oops")
        p4 = db.add("Will publish", PAST_TIME)
        db.mark_published(p4["id"], "urn:li:share:789")

        s = db.summary()
        assert s["counts"]["pending"] == 2
        assert s["counts"]["failed"] == 1
        assert s["counts"]["published"] == 1
        assert s["next_due"] is not None
        assert s["recent_failure"] is not None
        assert s["recent_failure"]["error_message"] == "oops"
