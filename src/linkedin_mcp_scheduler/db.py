"""SQLite-based storage for scheduled LinkedIn posts."""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

_DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), ".linkedin-mcp-scheduler")
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DB_DIR, "scheduled.db")

DB_PATH = os.environ.get("DB_PATH", _DEFAULT_DB_PATH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id TEXT PRIMARY KEY,
    commentary TEXT NOT NULL,
    url TEXT,
    visibility TEXT NOT NULL DEFAULT 'PUBLIC',
    scheduled_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    published_at TEXT,
    post_urn TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);
"""


class ScheduledPostsDB:
    """SQLite-backed scheduled posts storage."""

    def __init__(self, db_path: str = DB_PATH):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # -- Core CRUD -----------------------------------------------------------

    @staticmethod
    def _normalize_iso_time(value: str) -> str:
        """Normalize an ISO 8601 string so 'Z' becomes '+00:00'."""
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()

    def add(
        self,
        commentary: str,
        scheduled_time: str,
        url: str | None = None,
        visibility: str = "PUBLIC",
    ) -> dict[str, Any]:
        post_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        normalized_time = self._normalize_iso_time(scheduled_time)
        self._conn.execute(
            """INSERT INTO scheduled_posts
               (id, commentary, url, visibility, scheduled_time, status, created_at, retry_count)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, 0)""",
            (post_id, commentary, url, visibility, normalized_time, created_at),
        )
        self._conn.commit()
        return self.get(post_id)  # type: ignore[return-value]

    def get(self, post_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_posts WHERE status = ? ORDER BY scheduled_time ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_posts ORDER BY scheduled_time ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_due(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'pending' AND scheduled_time <= ? ORDER BY scheduled_time ASC",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Status transitions ---------------------------------------------------

    def mark_published(self, post_id: str, post_urn: str) -> dict[str, Any] | None:
        row = self.get(post_id)
        if not row or row["status"] != "pending":
            return None
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE scheduled_posts SET status = 'published', published_at = ?, post_urn = ? WHERE id = ?",
            (now, post_urn, post_id),
        )
        self._conn.commit()
        return self.get(post_id)

    def mark_failed(self, post_id: str, error_message: str) -> dict[str, Any] | None:
        row = self.get(post_id)
        if not row or row["status"] != "pending":
            return None
        self._conn.execute(
            "UPDATE scheduled_posts SET status = 'failed', error_message = ?, retry_count = retry_count + 1 WHERE id = ?",
            (error_message, post_id),
        )
        self._conn.commit()
        return self.get(post_id)

    def cancel(self, post_id: str) -> dict[str, Any] | None:
        row = self.get(post_id)
        if not row or row["status"] != "pending":
            return None
        self._conn.execute(
            "UPDATE scheduled_posts SET status = 'cancelled' WHERE id = ?",
            (post_id,),
        )
        self._conn.commit()
        return self.get(post_id)

    # -- Edit operations ------------------------------------------------------

    def update(
        self,
        post_id: str,
        commentary: str | None = None,
        url: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, Any] | None:
        """Update fields of a pending post. Only provided (non-None) fields are changed."""
        row = self.get(post_id)
        if not row or row["status"] != "pending":
            return None

        updates: list[str] = []
        params: list[Any] = []

        if commentary is not None:
            updates.append("commentary = ?")
            params.append(commentary)
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        if visibility is not None:
            updates.append("visibility = ?")
            params.append(visibility)

        if not updates:
            return row  # nothing to change

        params.append(post_id)
        self._conn.execute(
            f"UPDATE scheduled_posts SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        return self.get(post_id)

    def reschedule(self, post_id: str, scheduled_time: str) -> dict[str, Any] | None:
        """Change the scheduled_time of a pending post."""
        row = self.get(post_id)
        if not row or row["status"] != "pending":
            return None
        self._conn.execute(
            "UPDATE scheduled_posts SET scheduled_time = ? WHERE id = ?",
            (scheduled_time, post_id),
        )
        self._conn.commit()
        return self.get(post_id)

    def retry(
        self, post_id: str, scheduled_time: str | None = None
    ) -> dict[str, Any] | None:
        """Reset a failed post to pending, optionally with a new scheduled time."""
        row = self.get(post_id)
        if not row or row["status"] != "failed":
            return None
        new_time = scheduled_time or (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat()
        self._conn.execute(
            "UPDATE scheduled_posts SET status = 'pending', scheduled_time = ?, error_message = NULL WHERE id = ?",
            (new_time, post_id),
        )
        self._conn.commit()
        return self.get(post_id)

    # -- Summary --------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return counts by status, next due post, and most recent failure."""
        # Counts by status
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM scheduled_posts GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}

        # Next due pending post
        next_due = self._conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'pending' ORDER BY scheduled_time ASC LIMIT 1"
        ).fetchone()

        # Most recent failure
        recent_failure = self._conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'failed' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        return {
            "counts": counts,
            "next_due": dict(next_due) if next_due else None,
            "recent_failure": dict(recent_failure) if recent_failure else None,
        }

    def close(self) -> None:
        self._conn.close()


# -- Singleton ----------------------------------------------------------------

_db: ScheduledPostsDB | None = None


def get_db(db_path: str | None = None) -> ScheduledPostsDB:
    """Return the singleton DB instance, creating it on first call.

    If called with a different db_path than the existing singleton, the old
    connection is closed and a new one is created at the requested path.
    """
    global _db
    resolved = db_path or DB_PATH
    if _db is not None and _db._db_path != resolved:
        _db.close()
        _db = None
    if _db is None:
        _db = ScheduledPostsDB(resolved)
    return _db


def reset_db() -> None:
    """Close and reset the singleton (used in tests)."""
    global _db
    if _db is not None:
        _db.close()
    _db = None
