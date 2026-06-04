from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "static/js/modelPicker.js").read_text(
    encoding="utf-8"
)


def test_model_picker_options_expose_stable_automation_selectors():
    assert "row.dataset.testid = 'model-picker-option';" in SOURCE
    assert "row.dataset.modelId = m.mid || '';" in SOURCE
    assert "row.dataset.endpointId = m.endpointId;" in SOURCE
    assert "row.dataset.stale = m.stale ? 'true' : 'false';" in SOURCE


def test_model_picker_favorites_expose_model_and_state_selectors():
    assert "favDot.dataset.testid = 'model-picker-favorite';" in SOURCE
    assert "favDot.dataset.modelId = m.mid || '';" in SOURCE
    assert "favDot.dataset.favorited = on ? 'true' : 'false';" in SOURCE


def test_model_picker_sections_and_provider_groups_are_targetable():
    assert "el.dataset.testid = 'model-picker-section';" in SOURCE
    assert "el.dataset.section = _selectorKey(label);" in SOURCE
    assert "empty.dataset.testid = 'model-picker-empty';" in SOURCE
    assert "header.dataset.testid = 'model-picker-provider-header';" in SOURCE
    assert "header.dataset.provider = provider;" in SOURCE
    assert "header.dataset.collapsed = isCollapsed ? 'true' : 'false';" in SOURCE
    assert "group.dataset.testid = 'model-picker-provider-group';" in SOURCE
    assert "group.dataset.provider = provider;" in SOURCE
