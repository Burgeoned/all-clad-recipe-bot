"""All Google Drive I/O, as the bot account (OAuth refresh token).

Resolves the drive layout by name under the configured root folder, downloads inputs and
the template, uploads outputs into the recipe's category folder, and moves processed
sources into `completed/`. The `completed/` move is the pipeline's idempotency marker — a
file in `recipes_to_input/` is by definition unprocessed.
"""

from __future__ import annotations

import io
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .config import Settings
from .models import Category, DriveLayout, FileType, RenderedDocs, SourceFile

logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"

WORKING_SUBFOLDER = "claude"          # working folders may live here or at root
TEMPLATE_NAME = "recipe_template.docx"  # matched case-insensitively
INPUT_FOLDER_NAME = "recipes_to_input"
COMPLETED_FOLDER_NAME = "completed"
REJECTED_FOLDER_NAME = "rejected"     # non-recipes land here (auto-created)

# A recipe file is small text/pdf/docx. Anything larger is almost certainly a mistake;
# skip it rather than pull megabytes into memory and pay to send it to Claude.
MAX_INPUT_BYTES = 5 * 1024 * 1024

_EXT_TO_TYPE = {"txt": FileType.TXT, "pdf": FileType.PDF, "docx": FileType.DOCX}


class DriveError(Exception):
    """Raised when the drive layout can't be resolved or a required folder is missing."""


class DriveClient:
    def __init__(self, settings: Settings) -> None:
        creds = Credentials(
            token=None,
            refresh_token=settings.oauth_refresh_token,
            client_id=settings.oauth_client_id,
            client_secret=settings.oauth_client_secret,
            token_uri=TOKEN_URI,
            scopes=SCOPES,
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._root = settings.root_folder_id

    # --- layout resolution ---------------------------------------------------------

    def resolve_layout(self) -> DriveLayout:
        """Find every folder/file the pipeline needs, by name under the root folder."""
        root_children = self._children(self._root)

        category_folders: dict[Category, str] = {}
        for category in Category:
            folder_id = self._find_folder(root_children, category.value)
            if folder_id is not None:
                category_folders[category] = folder_id
        # `other` is the misfit bucket — create it on first need rather than requiring setup.
        if Category.OTHER not in category_folders:
            category_folders[Category.OTHER] = self._create_folder(Category.OTHER.value, self._root)

        missing = [c.value for c in Category if c not in category_folders]
        if missing:
            raise DriveError(f"Missing category folders under root: {', '.join(missing)}")

        template_id = self._find_file(root_children, TEMPLATE_NAME)
        if template_id is None:
            raise DriveError(f"Template '{TEMPLATE_NAME}' not found directly under the root folder.")

        input_id, completed_id, rejected_id = self._resolve_working_folders(root_children)

        # Sanity guard: input, completed, rejected, and every category folder must be distinct.
        # If two resolve to the same id (a misnamed/duplicate folder), a "move" could land
        # somewhere unexpected — fail loudly instead.
        folder_ids = [input_id, completed_id, rejected_id, *category_folders.values()]
        if len(set(folder_ids)) != len(folder_ids):
            raise DriveError(
                "Resolved drive folders are not all distinct — check for duplicate or "
                "misnamed folders under the root."
            )

        return DriveLayout(
            input_folder_id=input_id,
            completed_folder_id=completed_id,
            rejected_folder_id=rejected_id,
            category_folders=category_folders,
            template_file_id=template_id,
        )

    def _resolve_working_folders(self, root_children: list[dict]) -> tuple[str, str, str]:
        """recipes_to_input + completed + rejected live at root or inside a `claude/` subfolder.
        input and completed must already exist; rejected is auto-created next to completed."""
        input_id = self._find_folder(root_children, INPUT_FOLDER_NAME)
        completed_id = self._find_folder(root_children, COMPLETED_FOLDER_NAME)
        rejected_id = self._find_folder(root_children, REJECTED_FOLDER_NAME)
        working_parent = self._root
        if input_id is None or completed_id is None or rejected_id is None:
            working_id = self._find_folder(root_children, WORKING_SUBFOLDER)
            if working_id is not None:
                working_parent = working_id
                working_children = self._children(working_id)
                input_id = input_id or self._find_folder(working_children, INPUT_FOLDER_NAME)
                completed_id = completed_id or self._find_folder(working_children, COMPLETED_FOLDER_NAME)
                rejected_id = rejected_id or self._find_folder(working_children, REJECTED_FOLDER_NAME)
        if input_id is None:
            raise DriveError(f"'{INPUT_FOLDER_NAME}' folder not found (root or {WORKING_SUBFOLDER}/).")
        if completed_id is None:
            raise DriveError(f"'{COMPLETED_FOLDER_NAME}' folder not found (root or {WORKING_SUBFOLDER}/).")
        # Create rejected/ alongside completed if it doesn't exist yet.
        if rejected_id is None:
            rejected_id = self._create_folder(REJECTED_FOLDER_NAME, working_parent)
        return input_id, completed_id, rejected_id

    # --- reads ---------------------------------------------------------------------

    def list_input_files(self, input_folder_id: str) -> list[SourceFile]:
        """Download every supported recipe file in the input folder. Unsupported types are
        skipped with a warning rather than failing the run."""
        sources: list[SourceFile] = []
        for entry in self._children(input_folder_id):
            if entry["mimeType"] == FOLDER_MIME:
                continue
            name = entry["name"]
            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            file_type = _EXT_TO_TYPE.get(extension)
            if file_type is None:
                logger.warning("Skipping unsupported input file: %s", name)
                continue
            size = int(entry.get("size") or 0)
            if size > MAX_INPUT_BYTES:
                logger.warning("Skipping oversized input file (%d bytes): %s", size, name)
                continue
            sources.append(
                SourceFile(
                    file_id=entry["id"],
                    name=name,
                    file_type=file_type,
                    content=self._download(entry["id"]),
                )
            )
        return sources

    def fetch_template(self, template_file_id: str) -> bytes:
        return self._download(template_file_id)

    # --- writes --------------------------------------------------------------------

    def upload_output(self, docs: RenderedDocs, base_name: str, folder_id: str) -> str:
        """Upload <base_name>.docx (+ .pdf if present) into folder_id. Returns the docx id."""
        docx_id = self._upload_bytes(docs.docx, f"{base_name}.docx", DOCX_MIME, folder_id)
        if docs.pdf is not None:
            self._upload_bytes(docs.pdf, f"{base_name}.pdf", PDF_MIME, folder_id)
        return docx_id

    def move_to_completed(self, file_id: str, completed_folder_id: str) -> None:
        """Reparent a processed source into completed/."""
        self._reparent(file_id, completed_folder_id)

    def move_to_rejected(self, file_id: str, rejected_folder_id: str) -> None:
        """Reparent a non-recipe source into rejected/ (out of the input queue)."""
        self._reparent(file_id, rejected_folder_id)

    def _reparent(self, file_id: str, dest_folder_id: str) -> None:
        current = self._service.files().get(fileId=file_id, fields="parents").execute()
        self._service.files().update(
            fileId=file_id,
            addParents=dest_folder_id,
            removeParents=",".join(current.get("parents", [])),
            fields="id",
        ).execute()

    # --- low-level helpers ---------------------------------------------------------

    def _children(self, folder_id: str) -> list[dict]:
        files: list[dict] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="nextPageToken, files(id,name,mimeType,size)",
                    pageSize=100,
                    pageToken=page_token,
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files

    @staticmethod
    def _find_folder(children: list[dict], name: str) -> str | None:
        for entry in children:
            if entry["mimeType"] == FOLDER_MIME and entry["name"].lower() == name.lower():
                return entry["id"]
        return None

    @staticmethod
    def _find_file(children: list[dict], name: str) -> str | None:
        for entry in children:
            if entry["mimeType"] != FOLDER_MIME and entry["name"].lower() == name.lower():
                return entry["id"]
        return None

    def _create_folder(self, name: str, parent_id: str) -> str:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        return self._service.files().create(body=body, fields="id").execute()["id"]

    def _download(self, file_id: str) -> bytes:
        return self._service.files().get_media(fileId=file_id).execute()

    def _upload_bytes(self, data: bytes, name: str, mime_type: str, folder_id: str) -> str:
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
        body = {"name": name, "parents": [folder_id]}
        return (
            self._service.files()
            .create(body=body, media_body=media, fields="id")
            .execute()["id"]
        )
