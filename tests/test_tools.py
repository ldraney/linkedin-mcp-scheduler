"""Tests for scheduling tool functions — validates JSON output and input validation."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from linkedin_mcp_scheduler.db import reset_db, get_db


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path, monkeypatch):
    """Point the DB singleton at a temp file for every test."""
    db_path = os.path.join(str(tmp_path), "test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    # Reset to force re-creation with new path
    reset_db()
    # Re-import to pick up the new env
    import linkedin_mcp_scheduler.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    reset_db()
    yield
    reset_db()


FUTURE_TIME = "2099-12-31T23:59:59Z"
FUTURE_TIME_NORMALIZED = "2099-12-31T23:59:59+00:00"
PAST_TIME = "2000-01-01T00:00:00Z"


class TestSchedulePost:
    def test_schedule_returns_valid_json(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post
        result = json.loads(schedule_post("Hello!", FUTURE_TIME))
        assert "postId" in result
        assert result["status"] == "pending"
        assert result["scheduledTime"] == FUTURE_TIME_NORMALIZED

    def test_schedule_rejects_past_time(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post
        result = json.loads(schedule_post("Hello!", PAST_TIME))
        assert result["error"] is True
        assert "future" in result["message"]

    def test_schedule_with_url(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post
        result = json.loads(schedule_post("Check it", FUTURE_TIME, url="https://example.com"))
        assert "postId" in result


class TestListScheduledPosts:
    def test_list_returns_valid_json(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, list_scheduled_posts
        schedule_post("Post 1", FUTURE_TIME)
        result = json.loads(list_scheduled_posts())
        assert result["count"] == 1
        assert len(result["posts"]) == 1

    def test_list_with_status_filter(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, list_scheduled_posts, cancel_scheduled_post
        r = json.loads(schedule_post("Post 1", FUTURE_TIME))
        schedule_post("Post 2", FUTURE_TIME)
        cancel_scheduled_post(r["postId"])

        pending = json.loads(list_scheduled_posts(status="pending"))
        assert pending["count"] == 1

        cancelled = json.loads(list_scheduled_posts(status="cancelled"))
        assert cancelled["count"] == 1


class TestGetScheduledPost:
    def test_get_existing(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, get_scheduled_post
        created = json.loads(schedule_post("My post", FUTURE_TIME))
        result = json.loads(get_scheduled_post(created["postId"]))
        assert result["post"]["commentary"] == "My post"

    def test_get_nonexistent(self):
        from linkedin_mcp_scheduler.tools.scheduling import get_scheduled_post
        result = json.loads(get_scheduled_post("nonexistent-uuid"))
        assert result["error"] is True


class TestCancelScheduledPost:
    def test_cancel_pending(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, cancel_scheduled_post
        created = json.loads(schedule_post("Cancel me", FUTURE_TIME))
        result = json.loads(cancel_scheduled_post(created["postId"]))
        assert result["success"] is True
        assert result["status"] == "cancelled"

    def test_cancel_nonexistent(self):
        from linkedin_mcp_scheduler.tools.scheduling import cancel_scheduled_post
        result = json.loads(cancel_scheduled_post("nonexistent"))
        assert result["error"] is True


class TestUpdateScheduledPost:
    def test_update_commentary(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, update_scheduled_post
        created = json.loads(schedule_post("Original", FUTURE_TIME))
        result = json.loads(update_scheduled_post(created["postId"], commentary="Updated"))
        assert result["success"] is True
        assert result["post"]["commentary"] == "Updated"

    def test_update_nonexistent(self):
        from linkedin_mcp_scheduler.tools.scheduling import update_scheduled_post
        result = json.loads(update_scheduled_post("nonexistent", commentary="Nope"))
        assert result["error"] is True


class TestReschedulePost:
    def test_reschedule_valid(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, reschedule_post
        created = json.loads(schedule_post("Post", FUTURE_TIME))
        new_time = "2099-06-15T10:00:00Z"
        new_time_normalized = "2099-06-15T10:00:00+00:00"
        result = json.loads(reschedule_post(created["postId"], new_time))
        assert result["success"] is True
        assert result["post"]["scheduled_time"] == new_time_normalized

    def test_reschedule_rejects_past_time(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, reschedule_post
        created = json.loads(schedule_post("Post", FUTURE_TIME))
        result = json.loads(reschedule_post(created["postId"], PAST_TIME))
        assert result["error"] is True
        assert "future" in result["message"]


class TestRetryFailedPost:
    def test_retry_failed(self):
        from linkedin_mcp_scheduler.tools.scheduling import retry_failed_post
        # Manually create and fail a post via the DB
        db = get_db()
        post = db.add("Failed post", PAST_TIME)
        db.mark_failed(post["id"], "API error")

        result = json.loads(retry_failed_post(post["id"]))
        assert result["success"] is True
        assert result["post"]["status"] == "pending"

    def test_retry_non_failed(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, retry_failed_post
        created = json.loads(schedule_post("Not failed", FUTURE_TIME))
        result = json.loads(retry_failed_post(created["postId"]))
        assert result["error"] is True


class TestQueueSummary:
    def test_empty_summary(self):
        from linkedin_mcp_scheduler.tools.scheduling import queue_summary
        result = json.loads(queue_summary())
        assert "summary" in result
        assert result["counts"] == {}

    def test_summary_with_data(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, queue_summary
        schedule_post("Post 1", FUTURE_TIME)
        schedule_post("Post 2", FUTURE_TIME)

        result = json.loads(queue_summary())
        assert result["counts"]["pending"] == 2
        assert result["next_due"] is not None


class TestISOTimeNormalization:
    """Verify that Z-suffixed times are normalized to +00:00 throughout the tools layer."""

    def test_schedule_post_normalizes_z_suffix(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, get_scheduled_post
        result = json.loads(schedule_post("Z test", "2099-07-01T12:00:00Z"))
        assert result["scheduledTime"] == "2099-07-01T12:00:00+00:00"
        # Verify the stored value in the DB matches
        post = json.loads(get_scheduled_post(result["postId"]))
        assert post["post"]["scheduled_time"] == "2099-07-01T12:00:00+00:00"

    def test_reschedule_normalizes_z_suffix(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post, reschedule_post
        created = json.loads(schedule_post("Reschedule Z", FUTURE_TIME))
        result = json.loads(reschedule_post(created["postId"], "2099-08-01T09:00:00Z"))
        assert result["post"]["scheduled_time"] == "2099-08-01T09:00:00+00:00"

    def test_retry_normalizes_z_suffix(self):
        from linkedin_mcp_scheduler.tools.scheduling import retry_failed_post
        db = get_db()
        post = db.add("Retry Z", PAST_TIME)
        db.mark_failed(post["id"], "error")

        result = json.loads(retry_failed_post(post["id"], scheduled_time="2099-09-01T15:00:00Z"))
        assert result["post"]["scheduled_time"] == "2099-09-01T15:00:00+00:00"

    def test_plus_offset_times_pass_through_unchanged(self):
        from linkedin_mcp_scheduler.tools.scheduling import schedule_post
        result = json.loads(schedule_post("Offset test", "2099-10-01T08:00:00+00:00"))
        assert result["scheduledTime"] == "2099-10-01T08:00:00+00:00"
