"""OAuth 2.0 credential handling for the YouTube Data API.

Two paths are supported:

1. **CI / headless** (the daily run): credentials are rebuilt from a long-lived
   refresh token plus the OAuth client id/secret, all provided as environment
   variables. No browser, no interaction.

2. **Local first-time setup**: ``scripts/authorize.py`` runs an installed-app
   browser flow once and persists ``token.json`` (and prints the refresh token
   you paste into your CI secrets).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Full read/write access is needed to create playlists and add items.
SCOPES = ["https://www.googleapis.com/auth/youtube"]

TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_TOKEN_FILE = "token.json"


class AuthError(RuntimeError):
    """Raised when usable credentials cannot be assembled."""


def _from_env() -> Credentials | None:
    """Build credentials from environment variables, or return None if unset."""
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def _from_token_file(path: str | Path) -> Credentials | None:
    path = Path(path)
    if not path.exists():
        return None
    return Credentials.from_authorized_user_info(json.loads(path.read_text()), SCOPES)


def get_credentials(token_file: str | Path = DEFAULT_TOKEN_FILE) -> Credentials:
    """Return refreshed, ready-to-use credentials.

    Resolution order: environment variables (CI) first, then a local
    ``token.json``. The token is force-refreshed so the API client always gets a
    valid access token.
    """
    creds = _from_env() or _from_token_file(token_file)
    if creds is None:
        raise AuthError(
            "No credentials found. Set YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / "
            "YOUTUBE_REFRESH_TOKEN, or run scripts/authorize.py to create token.json."
        )

    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError("Credentials have no refresh token; re-run scripts/authorize.py.")
        creds.refresh(Request())

    return creds
