import pytest
from pydantic import ValidationError

from recipe_pipeline.models import Category, Ingredient, IngredientGroup, Recipe


def _minimal(**overrides) -> dict:
    data = {
        "title": "Test",
        "category": "pork",
        "ingredient_groups": [IngredientGroup(ingredients=[Ingredient(item="flour")])],
        "steps": ["Mix."],
        "source_filename": "f.txt",
    }
    data.update(overrides)
    return data


def test_recipe_minimal_valid():
    recipe = Recipe(**_minimal())
    assert recipe.category is Category.PORK
    assert recipe.description is None
    assert recipe.dietary_tags == []
    assert recipe.nutrition is None


def test_category_string_coerces_to_enum():
    assert Recipe(**_minimal(category="seafood")).category is Category.SEAFOOD


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        Recipe(**_minimal(category="fish"))


def test_missing_required_field_rejected():
    data = _minimal()
    del data["title"]
    with pytest.raises(ValidationError):
        Recipe(**data)
