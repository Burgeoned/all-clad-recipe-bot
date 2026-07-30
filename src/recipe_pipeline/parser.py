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
REJECT_TOOL_NAME = "not_a_recipe"
MAX_ATTEMPTS = 2
# A recipe is at most a few thousand characters. Far beyond that isn't a recipe — refuse
# rather than pay to send a novel to the model.
MAX_INPUT_CHARS = 100_000

SYSTEM_PROMPT = """You extract structured data from a recipe and classify it.

You are given the raw text of a file dropped into a recipes folder. First decide whether it \
is actually a recipe. If it is NOT a recipe — e.g. an invoice, a letter, an essay, a grocery \
list, notes, or unreadable/garbage text — call the `not_a_recipe` tool with a short reason and \
stop. Do NOT invent a recipe from non-recipe text. Only if it IS a recipe, call `save_recipe` \
exactly once with the structured recipe. A messy or informal recipe still counts as a recipe; \
only reject things that clearly are not recipes at all.

Rules for `save_recipe`:
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
    """Raised when Claude cannot produce a valid structured recipe (a real error)."""


class NotARecipeError(Exception):
    """Raised when Claude determines the input isn't a recipe at all. Not an error — the
    file should be set aside, not retried."""


def parse_recipe(raw_text: str, source_filename: str, settings: Settings) -> Recipe:
    """Parse + classify recipe text into a validated `Recipe`.

    Raises NotARecipeError if the text clearly isn't a recipe; ParseError on an API failure,
    a safety refusal, a missing tool call, or output that still doesn't validate after one retry.
    """
    if not raw_text.strip():
        raise ParseError(f"{source_filename} has no extractable text.")
    if len(raw_text) > MAX_INPUT_CHARS:
        raise NotARecipeError(
            f"{source_filename} is too long ({len(raw_text)} chars) to be a recipe."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tools = [
        {
            "name": TOOL_NAME,
            "description": "Record the fully structured, categorized recipe from the source text.",
            "input_schema": Recipe.model_json_schema(),
        },
        {
            "name": REJECT_TOOL_NAME,
            "description": "Use when the text is NOT a recipe (invoice, letter, notes, garbage, etc.).",
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "Why it isn't a recipe."}},
                "required": ["reason"],
            },
        },
    ]
    messages: list[dict] = [
        {"role": "user", "content": f"Source filename: {source_filename}\n\nFile text:\n{raw_text}"}
    ]

    last_error: ValidationError | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.messages.create(
                model=settings.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                tool_choice={"type": "any"},  # must call save_recipe OR not_a_recipe
            )
        except anthropic.APIError as exc:
            raise ParseError(f"Claude API error while parsing {source_filename}: {exc}") from exc

        if response.stop_reason == "refusal":
            raise ParseError(f"Claude refused to parse {source_filename}.")

        reject = next(
            (b for b in response.content if b.type == "tool_use" and b.name == REJECT_TOOL_NAME), None
        )
        if reject is not None:
            reason = reject.input.get("reason", "not a recipe") if isinstance(reject.input, dict) else "not a recipe"
            raise NotARecipeError(f"{source_filename}: {reason}")

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
