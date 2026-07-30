from recipe_pipeline.models import Nutrition
from recipe_pipeline.validation import check_macros


def test_none_nutrition_no_warnings():
    assert check_macros(None) == []


def test_consistent_in_range_macros_pass():
    # 40*4 + 30*4 + 20*9 = 460 kcal, matches stated calories.
    nutrition = Nutrition(
        calories="460 kcal", protein="40 g", carbs="30 g", fat="20 g", fiber="3 g", sodium="500 mg"
    )
    assert check_macros(nutrition) == []


def test_calorie_mismatch_is_flagged():
    # Macros imply ~460 kcal but calories claim 900.
    nutrition = Nutrition(calories="900 kcal", protein="40 g", carbs="30 g", fat="20 g")
    assert any("add up" in w for w in check_macros(nutrition))


def test_out_of_range_value_is_flagged():
    nutrition = Nutrition(calories="9000 kcal", protein="40 g", carbs="30 g", fat="20 g")
    assert any("calories" in w for w in check_macros(nutrition))


def test_partial_values_do_not_false_alarm():
    # Only calories present, in range -> no consistency check, no warnings.
    assert check_macros(Nutrition(calories="500 kcal")) == []
