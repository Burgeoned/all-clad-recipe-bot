import pytest

from recipe_pipeline.config import Settings
from recipe_pipeline.parser import MAX_INPUT_CHARS, NotARecipeError, ParseError, parse_recipe


def _settings() -> Settings:
    return Settings.from_env(
        {
            "GOOGLE_OAUTH_CLIENT_ID": "id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "secret-value",
            "GOOGLE_OAUTH_REFRESH_TOKEN": "1//refresh-value",
            "ROOT_FOLDER_ID": "root123",
            "ANTHROPIC_API_KEY": "sk-ant-value",
        }
    )


def test_parser_rejects_empty_text_without_api_call():
    with pytest.raises(ParseError):
        parse_recipe("   \n  ", "empty.txt", _settings())


def test_parser_rejects_oversized_text_without_api_call():
    # Absurdly long input is treated as "not a recipe" (set aside, not a hard error).
    with pytest.raises(NotARecipeError):
        parse_recipe("x" * (MAX_INPUT_CHARS + 1), "huge.txt", _settings())


def test_settings_repr_hides_secrets():
    text = repr(_settings())
    assert "secret-value" not in text
    assert "1//refresh-value" not in text
    assert "sk-ant-value" not in text
    assert "***" in text
    # Non-secret config is still visible for debugging.
    assert "root123" in text
