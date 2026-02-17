"""MCP server entry point — FastMCP app and LinkedInClient lifecycle."""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from linkedin_sdk import LinkedInClient

from .token_storage import build_linkedin_client

mcp = FastMCP("linkedin-scheduler")

# claude.ai requires persistent sessions — stateless mode creates sessions
# with ID None that terminate immediately, causing infinite reconnect loops.
mcp.settings.stateless_http = False

# ---------------------------------------------------------------------------
# Shared client instance
# ---------------------------------------------------------------------------

_client: LinkedInClient | None = None


def get_client() -> LinkedInClient:
    """Return the shared LinkedInClient, creating it on first call.

    Reads credentials from OS keychain first, falls back to env vars.
    """
    global _client
    if _client is None:
        _client = build_linkedin_client()
    return _client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(exc: Exception) -> str:
    """Format an exception into a user-friendly error string."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.json()
        except Exception:
            body = exc.response.text
        return json.dumps(
            {
                "error": True,
                "status_code": exc.response.status_code,
                "message": str(exc),
                "details": body,
            },
            indent=2,
        )
    return json.dumps({"error": True, "message": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Register tool modules — each module calls @mcp.tool() at import time
# ---------------------------------------------------------------------------

from .tools import register_all_tools  # noqa: E402

register_all_tools()


def main() -> None:
    """Entry point for the console script."""
    mcp.run()
