"""Turn extracted recipe text into a structured, categorized `Recipe` via Claude.

One Claude call per recipe using a forced tool call whose input schema is the `Recipe`
schema. We validate the tool output with Pydantic ourselves (rather than strict structured
outputs, whose grammar compiler rejects this nested schema as "too complex"), and give the
model one bounded retry with the validation error fed back. Categorization happens in the
same call.
"""

from __future__ import annotations

import anthropic
from pydantic import ValidationError

from .config import Settings
from .models import Recipe

# Non-streaming: recipes are small, well under the ~16k token timeout threshold.
MAX_TOKENS = 8192
TOOL_NAME = "save_recipe"
MAX_ATTEMPTS = 2
# A recipe is at most a few thousand characters. Far beyond that isn't a recipe — refuse
# rather than pay to send a novel to the model.
MAX_INPUT_CHARS = 100_000

SYSTEM_PROMPT = """You extract structured data from a recipe and classify it.

You are given the raw text of a single recipe (from a .txt, .pdf, or .docx file). Call the \
`save_recipe` tool exactly once with the structured recipe.

Rules:
- Extract only what the source states. Do not invent ingredients, steps, times, or nutrition. \
Omit / use null for any field the source does not provide (e.g. leave nutrition fields null if \
the source has no nutrition info).
- Times are integers in minutes. Convert "1 hr 30 min" to 90. If a time isn't given, use null.
- `steps` is an ordered list; each entry is one instruction, in cooking order.
- Preserve ingredient sub-groups only if the recipe clearly has them (e.g. "For the sauce", \
"For the dough"); otherwise use a single group with heading = null.
- `substitutions` and `tips` capture any swap notes / pro-tips / variations the source mentions.

Classify the recipe into exactly one `category`:
- breakfast: breakfast/brunch dishes regardless of protein
- dessert: sweets, baked goods, desserts
- beef / pork / poultry / seafood: savory mains by their primary protein
- other: anything that fits none of the above (vegetarian/vegan mains, sides, drinks, sauces)
Choose by the dish's primary identity. A breakfast sausage dish is breakfast, not pork. A beef \
dessert is dessert, not beef. When a savory main has no clear meat, use other."""


class ParseError(Exception):
    """Raised when Claude cannot produce a valid structured recipe."""


def parse_recipe(raw_text: str, source_filename: str, settings: Settings) -> Recipe:
    """Parse + classify recipe text into a validated `Recipe`.

    Raises ParseError on an API failure, a safety refusal, a missing tool call, or output
    that still doesn't validate after one retry.
    """
    if not raw_text.strip():
        raise ParseError(f"{source_filename} has no extractable text.")
    if len(raw_text) > MAX_INPUT_CHARS:
        raise ParseError(
            f"{source_filename} is too long ({len(raw_text)} chars) to be a recipe; skipped."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tool = {
        "name": TOOL_NAME,
        "description": "Record the fully structured, categorized recipe from the source text.",
        "input_schema": Recipe.model_json_schema(),
    }
    messages: list[dict] = [
        {"role": "user", "content": f"Source filename: {source_filename}\n\nRecipe text:\n{raw_text}"}
    ]

    last_error: ValidationError | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.messages.create(
                model=settings.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[tool],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except anthropic.APIError as exc:
            raise ParseError(f"Claude API error while parsing {source_filename}: {exc}") from exc

        if response.stop_reason == "refusal":
            raise ParseError(f"Claude refused to parse {source_filename}.")

        tool_use = next(
            (b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME), None
        )
        if tool_use is None:
            raise ParseError(f"Claude did not call {TOOL_NAME} for {source_filename}.")

        try:
            recipe = Recipe.model_validate(tool_use.input)
        except ValidationError as exc:
            # Feed the validation error back and let Claude correct itself once.
            last_error = exc
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "is_error": True,
                    "content": f"That didn't validate:\n{exc}\nCall {TOOL_NAME} again with corrected data.",
                }],
            })
            continue

        # Provenance is ours to set, not the model's — guarantee it matches the real file.
        return recipe.model_copy(update={"source_filename": source_filename})

    raise ParseError(f"Recipe from {source_filename} failed validation after retry: {last_error}")
