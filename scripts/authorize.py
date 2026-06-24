#!/usr/bin/env python3
"""One-time OAuth setup — run this locally to mint a refresh token.

Usage:
    python scripts/authorize.py path/to/client_secret.json

What it does:
  1. Opens a browser so you can grant access to YOUR YouTube account.
  2. Writes ``token.json`` (used for local `--dry-run` testing).
  3. Prints the three values to paste into your GitHub repo secrets:
       YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN

The client_secret.json comes from Google Cloud Console:
  APIs & Services → Credentials → OAuth client ID → "Desktop app".
Make sure the "YouTube Data API v3" is enabled for the project.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/authorize.py <client_secret.json>", file=sys.stderr)
        return 2

    client_secret_path = Path(argv[1])
    if not client_secret_path.exists():
        print(f"Not found: {client_secret_path}", file=sys.stderr)
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    # Force a refresh token to be issued.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    Path("token.json").write_text(creds.to_json())

    secret_data = json.loads(client_secret_path.read_text())
    installed = secret_data.get("installed") or secret_data.get("web") or {}

    print("\n" + "=" * 60)
    print(" Saved token.json. Add these to your GitHub repo secrets:")
    print("   Settings → Secrets and variables → Actions → New secret")
    print("=" * 60)
    print(f"YOUTUBE_CLIENT_ID={installed.get('client_id', '')}")
    print(f"YOUTUBE_CLIENT_SECRET={installed.get('client_secret', '')}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
