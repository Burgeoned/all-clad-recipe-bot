"""One-time helper: exchange the OAuth *client* credentials for a long-lived *refresh token*.

Run this locally, once, signed in as the recipe-bot Google account (allcladclaude@gmail.com).
It opens a browser, you consent, and it prints the three values the pipeline needs. Paste
them into your local `.env` (and later into GitHub Actions secrets).

Why a separate script: the consent flow needs an interactive browser, which a headless CI
run cannot do. We do it once here; the pipeline itself only ever uses the resulting refresh
token to mint short-lived access tokens — the client-secret JSON never has to leave your
machine.

Usage:
    pip install google-auth-oauthlib
    python scripts/get_refresh_token.py "C:/path/to/client_secret_XXXX.json"
"""

from __future__ import annotations

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# Full Drive scope is required: the pipeline reads user-uploaded input files (which the app
# did NOT create, so narrower scopes like drive.file cannot see them), writes the output
# docs, and moves processed sources into completed/. No narrower scope covers all three.
SCOPES: list[str] = ["https://www.googleapis.com/auth/drive"]


def main(client_secret_path: str) -> None:
    path = Path(client_secret_path)
    if not path.is_file():
        sys.exit(f"Client secret file not found: {path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(path), scopes=SCOPES)

    # access_type=offline + prompt=consent forces Google to always return a refresh token.
    # Without them a repeat authorization can hand back only a short-lived access token.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        sys.exit(
            "No refresh token was returned. Re-run this script; if it keeps happening, "
            "revoke this app's access at https://myaccount.google.com/permissions (while "
            "signed in as the bot account) and try once more."
        )

    bar = "=" * 64
    print(f"\n{bar}")
    print("SUCCESS — copy these into your .env (and GitHub Actions secrets):")
    print(bar)
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print(bar)
    print("\nThese are long-lived credentials. Keep them out of git (.env is gitignored).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/get_refresh_token.py <client_secret.json>")
    main(sys.argv[1])
