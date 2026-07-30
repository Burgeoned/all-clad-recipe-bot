from recipe_pipeline.pipeline import _safe_base_name, _stem


def test_safe_base_name_strips_reserved_chars():
    assert _safe_base_name("Mom's Beef/Pork Stew: v2") == "Mom's Beef Pork Stew v2"


def test_safe_base_name_collapses_whitespace():
    assert _safe_base_name("  a   b  ") == "a b"


def test_safe_base_name_truncates():
    assert len(_safe_base_name("x" * 200)) == 100


def test_stem_removes_extension():
    assert _stem("grandmas_chicken.txt") == "grandmas_chicken"
    assert _stem("no_extension") == "no_extension"
    assert _stem("a.b.docx") == "a.b"
