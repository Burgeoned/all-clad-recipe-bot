"""CLI entrypoint: `python -m recipe_pipeline` (or the `recipe-pipeline` console script).

Exit code = number of files that failed (0 = clean), so a scheduled CI run surfaces
problems without extra plumbing. Setup errors (bad config, missing folders) exit 2.
"""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, Settings, load_dotenv
from .drive_client import DriveError
from .models import Status
from .pipeline import run

logger = logging.getLogger("recipe_pipeline")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Keep third-party HTTP/client chatter (which echoes request URLs) out of our logs.
    for noisy in ("httpx", "googleapiclient", "google", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_dotenv(".env")  # local convenience; in CI the vars come from the environment directly

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    try:
        results = run(settings)
    except DriveError as exc:
        logger.error("Drive setup error: %s", exc)
        return 2

    processed = sum(1 for r in results if r.status is Status.PROCESSED)
    failed = [r for r in results if r.status is Status.FAILED]
    mode = " (dry-run)" if settings.dry_run else ""
    logger.info("Done%s: %d processed, %d failed.", mode, processed, len(failed))
    for result in failed:
        logger.error("  FAILED %s: %s", result.source_name, result.error)

    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
