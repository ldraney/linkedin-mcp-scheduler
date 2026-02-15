"""Publisher daemon — polls for due posts and publishes them via LinkedInClient."""

from __future__ import annotations

import os
import signal
import sys
import time

from linkedin_sdk import LinkedInClient

from .db import get_db
from .token_storage import get_credentials

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

_shutdown = False


def _handle_signal(signum: int, frame: object) -> None:
    """Set the shutdown flag on SIGINT/SIGTERM."""
    global _shutdown
    _shutdown = True
    print(f"\nReceived signal {signum}, shutting down gracefully...")


def _get_client() -> LinkedInClient:
    """Build a LinkedInClient from keychain or env vars."""
    creds = get_credentials()
    if creds:
        return LinkedInClient(
            access_token=creds.get("accessToken"),
            person_id=creds.get("personId"),
        )
    return LinkedInClient()


def run_once() -> None:
    """Query due posts and publish each via LinkedInClient."""
    db = get_db()
    due = db.get_due()

    if not due:
        return

    client = _get_client()
    for post in due:
        try:
            kwargs: dict = {
                "commentary": post["commentary"],
                "visibility": post["visibility"],
            }
            if post.get("url"):
                kwargs["article_url"] = post["url"]

            result = client.create_post(**kwargs)
            post_urn = result.get("postUrn", result.get("id", "unknown"))
            db.mark_published(post["id"], post_urn)
            print(f"Published: {post['id']} -> {post_urn}")
        except Exception as e:
            db.mark_failed(post["id"], str(e))
            print(f"Failed: {post['id']} -> {e}")


def main() -> None:
    """Entry point for the publisher daemon.

    Runs run_once() in a loop with configurable poll interval.
    Shuts down gracefully on SIGINT or SIGTERM.
    """
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"Publisher daemon started (poll interval: {POLL_INTERVAL_SECONDS}s)")

    while not _shutdown:
        try:
            run_once()
        except Exception as e:
            print(f"Error in run_once: {e}", file=sys.stderr)

        # Sleep in small increments so we can react to shutdown quickly
        for _ in range(POLL_INTERVAL_SECONDS):
            if _shutdown:
                break
            time.sleep(1)

    print("Publisher daemon stopped.")
