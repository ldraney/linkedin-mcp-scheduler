"""Scheduling tools — schedule, list, get, cancel, update, reschedule, retry, summary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated

from pydantic import Field

from ..server import mcp, _error_response
from ..db import get_db


@mcp.tool()
def schedule_post(
    commentary: Annotated[str, Field(description="Post text content (max 3000 characters).")],
    scheduled_time: Annotated[str, Field(description="ISO 8601 datetime for when to publish, e.g. 2026-02-15T14:00:00Z. Must be in the future.")],
    url: Annotated[str | None, Field(description="Optional article URL to attach.")] = None,
    visibility: Annotated[str, Field(description="Post visibility: PUBLIC, CONNECTIONS, LOGGED_IN, or CONTAINER.")] = "PUBLIC",
) -> str:
    """Schedule a LinkedIn post for future publication.

    The post will be stored in the local database and published by the daemon
    when the scheduled time arrives. Run the daemon with `linkedin-mcp-scheduler-daemon`.
    """
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
        if scheduled_dt <= datetime.now(timezone.utc):
            return json.dumps({"error": True, "message": "scheduled_time must be in the future"})

        db = get_db()
        post = db.add(
            commentary=commentary,
            scheduled_time=scheduled_time,
            url=url,
            visibility=visibility,
        )
        return json.dumps({
            "postId": post["id"],
            "scheduledTime": post["scheduled_time"],
            "status": post["status"],
            "message": f"Post scheduled for {scheduled_time}",
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def list_scheduled_posts(
    status: Annotated[str | None, Field(description="Filter by status: pending, published, failed, or cancelled.")] = None,
    limit: Annotated[int, Field(description="Maximum number of posts to return.")] = 50,
) -> str:
    """List scheduled posts, optionally filtered by status."""
    try:
        db = get_db()
        posts = db.list(status=status, limit=limit)
        return json.dumps({
            "posts": posts,
            "count": len(posts),
            "message": f"Found {len(posts)} {status or 'all'} scheduled posts",
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def get_scheduled_post(
    post_id: Annotated[str, Field(description="The UUID of the scheduled post to retrieve.")],
) -> str:
    """Get details of a scheduled post by its UUID."""
    try:
        db = get_db()
        post = db.get(post_id)
        if not post:
            return json.dumps({"error": True, "message": f"Scheduled post not found: {post_id}"})
        return json.dumps({
            "post": post,
            "message": f"Status: {post['status']}",
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def cancel_scheduled_post(
    post_id: Annotated[str, Field(description="The UUID of the scheduled post to cancel.")],
) -> str:
    """Cancel a scheduled post (must be in pending status)."""
    try:
        db = get_db()
        result = db.cancel(post_id)
        if not result:
            return json.dumps({"error": True, "message": f"Post not found or not in pending status: {post_id}"})
        return json.dumps({
            "postId": result["id"],
            "status": "cancelled",
            "message": "Scheduled post cancelled successfully",
            "success": True,
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def update_scheduled_post(
    post_id: Annotated[str, Field(description="The UUID of the scheduled post to update.")],
    commentary: Annotated[str | None, Field(description="New post text content.")] = None,
    url: Annotated[str | None, Field(description="New article URL to attach.")] = None,
    visibility: Annotated[str | None, Field(description="New visibility: PUBLIC, CONNECTIONS, LOGGED_IN, or CONTAINER.")] = None,
) -> str:
    """Edit fields of a pending scheduled post in-place. Only provided fields are updated."""
    try:
        db = get_db()
        result = db.update(
            post_id,
            commentary=commentary,
            url=url,
            visibility=visibility,
        )
        if not result:
            return json.dumps({"error": True, "message": f"Post not found or not in pending status: {post_id}"})
        return json.dumps({
            "post": result,
            "message": "Scheduled post updated successfully",
            "success": True,
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def reschedule_post(
    post_id: Annotated[str, Field(description="The UUID of the scheduled post to reschedule.")],
    scheduled_time: Annotated[str, Field(description="New ISO 8601 datetime for when to publish. Must be in the future.")],
) -> str:
    """Change the scheduled time of a pending post."""
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
        if scheduled_dt <= datetime.now(timezone.utc):
            return json.dumps({"error": True, "message": "scheduled_time must be in the future"})

        db = get_db()
        result = db.reschedule(post_id, scheduled_time)
        if not result:
            return json.dumps({"error": True, "message": f"Post not found or not in pending status: {post_id}"})
        return json.dumps({
            "post": result,
            "message": f"Post rescheduled to {scheduled_time}",
            "success": True,
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def retry_failed_post(
    post_id: Annotated[str, Field(description="The UUID of the failed post to retry.")],
    scheduled_time: Annotated[str | None, Field(description="Optional new ISO 8601 datetime. Defaults to now + 5 minutes.")] = None,
) -> str:
    """Reset a failed post to pending so it will be retried by the daemon."""
    try:
        if scheduled_time:
            scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
            if scheduled_dt <= datetime.now(timezone.utc):
                return json.dumps({"error": True, "message": "scheduled_time must be in the future"})

        db = get_db()
        result = db.retry(post_id, scheduled_time=scheduled_time)
        if not result:
            return json.dumps({"error": True, "message": f"Post not found or not in failed status: {post_id}"})
        return json.dumps({
            "post": result,
            "message": f"Post reset to pending, scheduled for {result['scheduled_time']}",
            "success": True,
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)


@mcp.tool()
def queue_summary() -> str:
    """Get a formatted overview of the scheduling queue: counts by status, next due post, and most recent failure."""
    try:
        db = get_db()
        summary = db.summary()

        counts = summary["counts"]
        lines = ["Queue Summary", "=============", ""]

        # Status counts
        total = sum(counts.values())
        lines.append(f"Total posts: {total}")
        for status in ["pending", "published", "failed", "cancelled"]:
            lines.append(f"  {status}: {counts.get(status, 0)}")
        lines.append("")

        # Next due
        if summary["next_due"]:
            nd = summary["next_due"]
            lines.append(f"Next due: {nd['scheduled_time']}")
            preview = nd["commentary"][:80] + ("..." if len(nd["commentary"]) > 80 else "")
            lines.append(f"  \"{preview}\"")
        else:
            lines.append("Next due: none")
        lines.append("")

        # Recent failure
        if summary["recent_failure"]:
            rf = summary["recent_failure"]
            lines.append(f"Most recent failure: {rf['id']}")
            lines.append(f"  Error: {rf.get('error_message', 'unknown')}")
            lines.append(f"  Retries: {rf['retry_count']}")
        else:
            lines.append("No recent failures.")

        formatted = "\n".join(lines)
        return json.dumps({
            "summary": formatted,
            "counts": counts,
            "next_due": summary["next_due"],
            "recent_failure": summary["recent_failure"],
        }, indent=2)
    except Exception as exc:
        return _error_response(exc)
