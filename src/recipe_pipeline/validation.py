"""Deterministic sanity checks for recipe macros.

The model can misjudge or fat-finger numbers, so we independently verify per-serving macros:
they should fall in plausible ranges, and calories should roughly match the Atwater estimate
(4 kcal/g protein, 4 kcal/g carbs, 9 kcal/g fat). Anything off returns a human-readable
warning; it never blocks — the recipe still gets filed, just flagged.
"""

from __future__ import annotations

import re

from .models import Nutrition

_NUMBER = re.compile(r"[-+]?\d*\.?\d+")

# Plausible per-serving ranges. Generous on purpose — only catch clearly-wrong values.
_RANGES: dict[str, tuple[float, float]] = {
    "calories": (20, 2500),   # kcal
    "protein": (0, 250),      # g
    "carbs": (0, 400),        # g
    "fat": (0, 250),          # g
    "fiber": (0, 100),        # g
    "sodium": (0, 10000),     # mg
}

# Calories may legitimately differ from the Atwater estimate (alcohol, sugar alcohols,
# rounding); only flag a sizeable mismatch.
_CALORIE_TOLERANCE = 0.30


def _to_number(value: str | None) -> float | None:
    if not value:
        return None
    match = _NUMBER.search(value)
    return float(match.group()) if match else None


def check_macros(nutrition: Nutrition | None) -> list[str]:
    """Return warnings for implausible or internally-inconsistent per-serving macros."""
    if nutrition is None:
        return []

    values = {name: _to_number(getattr(nutrition, name)) for name in _RANGES}
    warnings: list[str] = []

    for name, (low, high) in _RANGES.items():
        value = values[name]
        if value is not None and not (low <= value <= high):
            warnings.append(
                f"{name} per serving looks off: {value:g} (expected roughly {low:g}–{high:g})"
            )

    calories, protein, carbs, fat = (values["calories"], values["protein"], values["carbs"], values["fat"])
    if None not in (calories, protein, carbs, fat) and calories > 0:
        implied = 4 * protein + 4 * carbs + 9 * fat
        if abs(implied - calories) / calories > _CALORIE_TOLERANCE:
            warnings.append(
                f"macros don't add up: {protein:g}g protein / {carbs:g}g carbs / {fat:g}g fat "
                f"imply ~{implied:g} kcal, but calories say {calories:g}"
            )

    return warnings
