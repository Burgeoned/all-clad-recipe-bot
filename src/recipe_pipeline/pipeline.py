"""Orchestration: resolve the drive layout, then process each input file in isolation.

One bad file never aborts the batch — each file's failure is captured as a FAILED
ProcessResult and the run continues. On success, the output lands in the recipe's category
folder and the source moves to completed/ (which is what marks it done).
"""

from __future__ import annotations

import logging
import re

from googleapiclient.errors import HttpError

from .config import Settings
from .drive_client import DriveClient, DriveError
from .extractors import ExtractionError, extract_text
from .models import DriveLayout, ProcessResult, SourceFile, Status
from .parser import ParseError, parse_recipe
from .renderer import RenderError, render
from .ssl_support import use_system_trust_store

logger = logging.getLogger(__name__)

# Errors that should fail a single file without aborting the batch.
_FILE_ERRORS = (ExtractionError, ParseError, RenderError, HttpError)


def run(settings: Settings) -> list[ProcessResult]:
    """Process new files in the input folder. Returns one result per file attempted."""
    use_system_trust_store()  # route TLS through the OS store before any network call

    client = DriveClient(settings)
    layout = client.resolve_layout()
    template = client.fetch_template(layout.template_file_id)
    sources = client.list_input_files(layout.input_folder_id)

    if not sources:
        logger.info("No new files in the input folder.")
        return []

    batch = sources[: settings.max_files_per_run]
    if len(sources) > len(batch):
        logger.info("Found %d files; processing %d this run.", len(sources), len(batch))

    return [_process_one(source, template, layout, client, settings) for source in batch]


def _process_one(
    source: SourceFile,
    template: bytes,
    layout: DriveLayout,
    client: DriveClient,
    settings: Settings,
) -> ProcessResult:
    try:
        text = extract_text(source)
        recipe = parse_recipe(text, source.name, settings)
        docs = render(recipe, template, settings)
        base_name = _safe_base_name(recipe.title) or _stem(source.name)
        folder_id = layout.category_folders[recipe.category]

        if settings.dry_run:
            logger.info(
                "[dry-run] %s -> %s: would save '%s.docx' and move source to completed/",
                source.name, recipe.category.value, base_name,
            )
            return ProcessResult(source.name, Status.PROCESSED, recipe.category)

        output_id = client.upload_output(docs, base_name, folder_id)
        # Move only after a confirmed upload, so a mid-run failure leaves the source queued.
        client.move_to_completed(source.file_id, layout.completed_folder_id)
        logger.info("Processed %s -> %s/%s.docx", source.name, recipe.category.value, base_name)
        return ProcessResult(source.name, Status.PROCESSED, recipe.category, output_id)

    except _FILE_ERRORS as exc:
        logger.error("Failed to process %s: %s", source.name, exc)
        return ProcessResult(source.name, Status.FAILED, error=str(exc))


def _safe_base_name(title: str) -> str:
    """Make a recipe title safe to use as a Drive filename (no path/reserved chars)."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:100]


def _stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0] if "." in filename else filename
