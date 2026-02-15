"""Credential storage — keychain first via keyring, env vars fallback.

Reads LinkedIn OAuth credentials from:
1. OS keychain (macOS Keychain, Windows Credential Manager, Linux Secret Service)
2. Environment variables LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_ID
"""

from __future__ import annotations

import json
import os
from typing import Any

import keyring

# Deliberately matches linkedin-mcp's keychain entry so both projects share credentials.
SERVICE_NAME = "linkedin-mcp"
ACCOUNT_NAME = "oauth-credentials"


def get_credentials() -> dict[str, Any] | None:
    """Retrieve OAuth credentials from OS keychain, falling back to env vars."""
    try:
        data = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if data:
            return json.loads(data)
    except Exception:
        pass

    access_token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    person_id = os.environ.get("LINKEDIN_PERSON_ID")
    if access_token:
        return {"accessToken": access_token, "personId": person_id}
    return None


def build_linkedin_client() -> "LinkedInClient":
    """Build a LinkedInClient from keychain or env vars.

    Shared by server.py (singleton) and daemon.py (per-cycle).
    """
    from linkedin_sdk import LinkedInClient

    creds = get_credentials()
    if creds:
        return LinkedInClient(
            access_token=creds.get("accessToken"),
            person_id=creds.get("personId"),
        )
    return LinkedInClient()
