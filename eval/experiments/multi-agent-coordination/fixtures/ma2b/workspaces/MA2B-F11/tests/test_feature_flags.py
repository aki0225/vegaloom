from src.feature_flags import render_feature_state


def test_render_feature_state_reports_enabled_state() -> None:
    assert render_feature_state(True) == "enabled"
