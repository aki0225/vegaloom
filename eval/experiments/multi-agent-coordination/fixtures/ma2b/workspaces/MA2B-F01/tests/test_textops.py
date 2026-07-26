from src.textops import normalize_label


def test_normalize_label_collapses_internal_whitespace() -> None:
    assert normalize_label("  alpha   beta  ") == "alpha beta"
