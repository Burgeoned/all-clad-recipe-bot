"""List the contents of ROOT_FOLDER_ID as the bot account.

Doubles as a credential smoke test: if this prints your folders, then OAuth, the folder
share, and ROOT_FOLDER_ID are all correct. Prints only names and ids — never secrets.

Usage:
    .venv\\Scripts\\python.exe scripts\\list_drive_ids.py
"""

from __future__ import annotations

import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from recipe_pipeline.config import load_dotenv
from recipe_pipeline.ssl_support import use_system_trust_store

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


def main() -> None:
    use_system_trust_store()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    root_id = os.environ["ROOT_FOLDER_ID"]

    root = service.files().get(fileId=root_id, fields="id,name").execute()
    print(f"\nRoot folder: {root['name']}   [{root['id']}]\n")

    resp = (
        service.files()
        .list(
            q=f"'{root_id}' in parents and trashed=false",
            fields="files(id,name,mimeType)",
            orderBy="folder,name",
            pageSize=100,
        )
        .execute()
    )
    files = resp.get("files", [])
    if not files:
        print("(no children — is the folder shared to the bot, and is ROOT_FOLDER_ID right?)")
        return

    for f in files:
        kind = "DIR " if f["mimeType"] == FOLDER_MIME else "FILE"
        print(f"  {kind}  {f['name']:<28} [{f['id']}]")
    print()


if __name__ == "__main__":
    main()
