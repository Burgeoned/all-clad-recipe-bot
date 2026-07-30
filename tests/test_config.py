import os
from pathlib import Path

import pytest

from recipe_pipeline.config import DEFAULT_MODEL, ConfigError, Settings, load_dotenv


def _write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_dotenv_value_that_is_only_a_comment_becomes_empty(tmp_path, monkeypatch):
    # The bug that bit us: `KEY=   # comment` must not make the value "# comment".
    monkeypatch.delenv("RECIPE_MODEL", raising=False)
    load_dotenv(_write_env(tmp_path, "RECIPE_MODEL=            # default: claude-haiku-4-5\n"))
    assert os.environ["RECIPE_MODEL"] == ""


def test_load_dotenv_strips_trailing_comment_but_keeps_value(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPORT_PDF", raising=False)
    load_dotenv(_write_env(tmp_path, "EXPORT_PDF=1  # 0 = docx only\n"))
    assert os.environ["EXPORT_PDF"] == "1"


def test_load_dotenv_quoted_value_kept_verbatim(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    load_dotenv(_write_env(tmp_path, 'ANTHROPIC_API_KEY="sk-ant-#not-a-comment"\n'))
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-#not-a-comment"


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_FOLDER_ID", "from-real-env")
    load_dotenv(_write_env(tmp_path, "ROOT_FOLDER_ID=from-file\n"))
    assert os.environ["ROOT_FOLDER_ID"] == "from-real-env"


def test_from_env_reports_all_missing_required():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({})
    message = str(exc.value)
    assert "ROOT_FOLDER_ID" in message
    assert "ANTHROPIC_API_KEY" in message


def test_from_env_defaults_and_booleans():
    settings = Settings.from_env(
        {
            "GOOGLE_OAUTH_CLIENT_ID": "a",
            "GOOGLE_OAUTH_CLIENT_SECRET": "b",
            "GOOGLE_OAUTH_REFRESH_TOKEN": "c",
            "ROOT_FOLDER_ID": "d",
            "ANTHROPIC_API_KEY": "e",
            "DRY_RUN": "1",
            "EXPORT_PDF": "0",
        }
    )
    assert settings.model == DEFAULT_MODEL
    assert settings.dry_run is True
    assert settings.export_pdf is False
