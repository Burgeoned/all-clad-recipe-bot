"""Runtime configuration, loaded and validated from environment variables.

`Settings` is frozen: config is read once at startup and never mutated. `from_env` is the
single place that reads the environment, so the rest of the code depends only on a typed
object, not on os.environ scattered around.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5"  # sparse volume → Haiku is plenty


class ConfigError(Exception):
    """Raised when required configuration is missing or malformed."""


def load_dotenv(path: str | Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (existing vars win, so CI
    secrets are never overwritten). Handles inline `# comments` and surrounding quotes.
    Intentionally tiny — a full dotenv dependency isn't warranted here."""
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] in "\"'" and stripped[-1] == stripped[0]:
            value = stripped[1:-1]  # quoted: take content verbatim
        else:
            # unquoted: a ` #...` tail (or a value that is only a comment) isn't the value.
            comment = value.find(" #")
            if comment != -1:
                value = value[:comment]
            value = value.strip()
            if value.startswith("#"):
                value = ""
        os.environ.setdefault(key, value)


@dataclass(frozen=True, repr=False)
class Settings:
    # --- OAuth (bot account: allcladclaude@gmail.com) ---
    oauth_client_id: str
    oauth_client_secret: str
    oauth_refresh_token: str
    # --- Drive ---
    root_folder_id: str           # the "all-clad chad" folder; all else resolved by name
    # --- Anthropic ---
    anthropic_api_key: str
    # --- optional ---
    model: str = DEFAULT_MODEL
    export_pdf: bool = True        # also export PDF via LibreOffice; off = docx only
    max_files_per_run: int = 10
    dry_run: bool = False          # parse + render but skip all Drive writes

    def __repr__(self) -> str:
        # Custom repr so credentials never appear in logs, tracebacks, or debug output.
        return (
            f"Settings(root_folder_id={self.root_folder_id!r}, model={self.model!r}, "
            f"export_pdf={self.export_pdf}, max_files_per_run={self.max_files_per_run}, "
            f"dry_run={self.dry_run}, oauth_client_id=***, oauth_client_secret=***, "
            f"oauth_refresh_token=***, anthropic_api_key=***)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build Settings from the environment (defaults to os.environ).

        Raises ConfigError listing every missing required var at once, so a misconfigured
        run fails with one clear message instead of one var at a time.
        """
        env = os.environ if env is None else env

        required = (
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
            "ROOT_FOLDER_ID",
            "ANTHROPIC_API_KEY",
        )
        missing = [name for name in required if not env.get(name, "").strip()]
        if missing:
            raise ConfigError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in (see README)."
            )

        return cls(
            oauth_client_id=env["GOOGLE_OAUTH_CLIENT_ID"].strip(),
            oauth_client_secret=env["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
            oauth_refresh_token=env["GOOGLE_OAUTH_REFRESH_TOKEN"].strip(),
            root_folder_id=env["ROOT_FOLDER_ID"].strip(),
            anthropic_api_key=env["ANTHROPIC_API_KEY"].strip(),
            model=env.get("RECIPE_MODEL", "").strip() or DEFAULT_MODEL,
            export_pdf=_parse_bool(env.get("EXPORT_PDF"), default=True),
            dry_run=_parse_bool(env.get("DRY_RUN"), default=False),
        )


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    """Parse a truthy/falsey env string. Empty/unset → default."""
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
