"""Data contracts for the recipe pipeline.

`Recipe` (and its nested pieces) is Pydantic because it is produced by the LLM and needs
validation + JSON schema/serialization. Everything else — transport structs the pipeline
passes between modules — is a plain frozen dataclass; those are simple, stable, and never
cross a validation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    """The output folders a recipe can be routed to. `OTHER` is the fallback for anything
    that fits none of the six; its Drive folder is auto-created if missing."""

    BREAKFAST = "breakfast"
    BEEF = "beef"
    DESSERT = "dessert"
    PORK = "pork"
    POULTRY = "poultry"
    SEAFOOD = "seafood"
    OTHER = "other"


class FileType(str, Enum):
    TXT = "txt"
    PDF = "pdf"
    DOCX = "docx"


class Status(str, Enum):
    PROCESSED = "processed"   # parsed, rendered, filed
    REJECTED = "rejected"     # not a recipe — moved to rejected/, not filed
    FAILED = "failed"         # transient error — left in the input queue to retry


# --------------------------------------------------------------------------------------
# LLM-produced recipe schema (Pydantic — validated)
# --------------------------------------------------------------------------------------


class Ingredient(BaseModel):
    quantity: str | None = Field(None, description="e.g. '2', '1 1/2'; None if 'to taste'")
    unit: str | None = Field(None, description="e.g. 'cup', 'g', 'tbsp'")
    item: str = Field(..., description="e.g. 'all-purpose flour'")
    note: str | None = Field(None, description="e.g. 'sifted', 'room temperature'")


class IngredientGroup(BaseModel):
    # Supports recipes with sub-components ("For the sauce", "For the dough").
    heading: str | None = None
    ingredients: list[Ingredient]


class Nutrition(BaseModel):
    # Per serving. Strings so a value can carry its unit, or be left absent.
    calories: str | None = None
    protein: str | None = None
    carbs: str | None = None
    fat: str | None = None
    fiber: str | None = None
    sodium: str | None = None


class Recipe(BaseModel):
    # --- header ---
    title: str
    description: str | None = None
    category: Category = Field(..., description="Claude classifies into one of the seven")
    # --- at a glance ---
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    total_time_min: int | None = None
    servings: str | None = Field(None, description="e.g. '10' or 'Makes 12 muffins'")
    # --- metadata line ---
    cuisine: str | None = None
    course: str | None = None
    difficulty: str | None = None
    dietary_tags: list[str] = Field(default_factory=list)
    source: str | None = Field(None, description="e.g. 'Custom R&D Recipe', a URL, a book")
    # --- body ---
    ingredient_groups: list[IngredientGroup]
    substitutions: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    steps: list[str] = Field(..., description="Ordered; each entry is one instruction")
    tips: list[str] = Field(default_factory=list, description="Tips & variations")
    nutrition: Nutrition | None = None
    # --- provenance (not user content) ---
    source_filename: str


# --------------------------------------------------------------------------------------
# Transport structs (dataclasses — internal, not validated)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    file_id: str
    name: str
    file_type: FileType
    content: bytes


@dataclass
class RenderedDocs:
    docx: bytes                 # filled template — always produced
    pdf: bytes | None = None    # LibreOffice export — only when export_pdf is on


@dataclass(frozen=True)
class DriveLayout:
    """Resolved once per run by listing children of the root 'all-clad chad' folder by name."""

    input_folder_id: str                    # recipes_to_input
    completed_folder_id: str                # completed
    rejected_folder_id: str                 # rejected (auto-created); non-recipes land here
    category_folders: dict[Category, str]   # includes OTHER (auto-created if missing)
    template_file_id: str                   # recipe_template.docx


@dataclass
class ProcessResult:
    source_name: str
    status: Status
    category: Category | None = None
    output_file_id: str | None = None       # the uploaded .docx file id
    error: str | None = None
