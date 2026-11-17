"""The `rag` UI toggle must be accepted.

do_ui_control advertises `rag` as a valid toggle in its own docstring and in
get_toggles ("Available toggles: web, bash, rag, ..."), and the frontend
fully wires it (chatStream.js maps rag -> rag-toggle / rag-indicator-btn).
But valid_toggles omitted "rag", so `toggle rag on` returned an "Unknown
toggle" error - the advertised capability was dead.
"""
import asyncio
import json
import pytest

from src.ai_interaction import do_ui_control
from routes import prefs_routes
from src.tool_schemas import function_call_to_tool_block


@pytest.mark.parametrize('name', ['Dark Red', "Artist's Night", 'Night "Sky"', 'minimal'])
def test_theme_native_adapter_preserves_name_and_colors(monkeypatch, name):
    stores = {}
    monkeypatch.setattr(prefs_routes, '_load_for_user', lambda owner: dict(stores))
    monkeypatch.setattr(prefs_routes, '_save_for_user', lambda owner, prefs: stores.update(prefs))
    colors = dict(bg='#1a0505', fg='#f5e6e6', panel='#2b0a0a', border='#4a1515', accent='#d93025')
    block = function_call_to_tool_block('ui_control', json.dumps(dict(
        action='create_theme', name=name, colors=colors)))
    result = asyncio.run(do_ui_control(block.content, owner='fixture'))
    assert 'error' not in result
    assert result['theme_name'] == name.lower().replace(' ', '-')
    assert result['colors']['bg'] == colors['bg']
    assert result['colors']['red'] == colors['accent']


def test_failed_theme_cannot_claim_success():
    from src.clean_agent_preview import failed_ui_completion
    failed = dict(tool='ui_control', error=True, execution_attempted=True,
                  output=json.dumps({'error': 'Invalid hex color'}))
    assert 'Invalid hex color' in failed_ui_completion('Dark Red theme created.', [failed])
    assert not failed_ui_completion('Could not create the theme.', [failed])
    assert not failed_ui_completion('Dark Red theme created.', [failed, dict(tool='ui_control', error=False)])


def test_toggle_rag_on_is_accepted():
    r = asyncio.run(do_ui_control("toggle rag on"))
    assert r.get("ui_event") == "toggle"
    assert r.get("toggle_name") == "rag"
    assert r.get("state") is True
    assert "error" not in r


def test_toggle_rag_off_is_accepted():
    r = asyncio.run(do_ui_control("toggle rag off"))
    assert r.get("toggle_name") == "rag"
    assert r.get("state") is False
    assert "error" not in r


def test_unknown_toggle_still_rejected():
    r = asyncio.run(do_ui_control("toggle bogus on"))
    assert "error" in r


def test_existing_toggle_still_works():
    r = asyncio.run(do_ui_control("toggle web on"))
    assert r.get("toggle_name") == "web" and r.get("state") is True


def test_open_calendar_panel_is_accepted():
    r = asyncio.run(do_ui_control("open_panel calendar"))
    assert r.get("ui_event") == "open_panel"
    assert r.get("panel") == "calendar"
    assert "error" not in r


def test_open_calendar_panel_accepts_view_and_target_date():
    r = asyncio.run(do_ui_control("open_panel calendar month 2026-09"))
    assert r.get("ui_event") == "open_panel"
    assert r.get("panel") == "calendar"
    assert r.get("view") == "month"
    assert r.get("target_date") == "2026-09"
    assert "error" not in r


def test_models_panel_alias_opens_cookbook_models_view():
    r = asyncio.run(do_ui_control("open_panel models"))
    assert r.get("panel") == "cookbook"
    assert r.get("view") == "Search"
    assert r.get("view_label") == "models"
    assert "models view" in r.get("results", "")


def test_cookbook_panel_accepts_named_subview():
    r = asyncio.run(do_ui_control("open_panel cookbook serve"))
    assert r.get("panel") == "cookbook"
    assert r.get("view") == "Serve"
    assert r.get("view_label") == "launch"


def test_set_theme_persists_owner_scoped_name_for_later_verification(monkeypatch):
    stores = {"alice": {}}
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: dict(stores.get(owner, {})))
    monkeypatch.setattr(prefs_routes, "_save_for_user", lambda owner, prefs: stores.__setitem__(owner, dict(prefs)))

    changed = asyncio.run(do_ui_control("set_theme dark", owner="alice"))
    current = asyncio.run(do_ui_control("get_theme", owner="alice"))

    assert changed.get("ui_event") == "set_theme"
    assert stores["alice"]["theme"] == {"name": "dark"}
    assert current['current_theme'] == 'dark'
    assert current['theme_known'] is True
    assert 'dark' in current['presets']
    assert 'ascii-fireflies' in current['background_patterns']


@pytest.mark.parametrize('pattern', ['none', 'embers', 'ascii-fireflies', 'starfield-depth', 'random'])
def test_minimal_theme_palette_and_background_survive_storage(monkeypatch, pattern):
    stores = {}
    monkeypatch.setattr(prefs_routes, '_load_for_user', lambda owner: dict(stores))
    monkeypatch.setattr(prefs_routes, '_save_for_user', lambda owner, prefs: stores.update(prefs))
    block = function_call_to_tool_block('ui_control', json.dumps({
        'action': 'create_theme', 'name': 'Night Sky',
        'colors': {'bg': '#123', 'accent': 'e34b50'},
        'background': {'pattern': pattern, 'speed': .5}}))
    result = asyncio.run(do_ui_control(block.content, owner='fixture'))
    assert 'error' not in result
    assert result['colors']['bg'] == '#112233'
    assert result['colors']['fg'] == '#ffffff'
    assert result['bg']['effectSpeed'] == .5
    chosen = result['bg']['pattern']
    assert chosen != 'random'
    assert chosen == pattern or pattern == 'random'
    assert stores['custom-themes']['night-sky']['bgPattern'] == chosen
    current = asyncio.run(do_ui_control('get_theme', owner='fixture'))
    assert current['background']['bgPattern'] == chosen
    assert current['colors']['bg'] == '#112233'
    assert current['custom_themes'] == ['night-sky']
    assert asyncio.run(do_ui_control('set_theme Night Sky', owner='fixture'))['theme_name'] == 'night-sky'


def test_theme_invalid_palette_and_storage_failure_are_not_success(monkeypatch):
    result = asyncio.run(do_ui_control(json.dumps({'action': 'create_theme', 'name': 'Bad',
        'colors': {'bg': 'potato', 'accent': '#fff'}})))
    assert 'colors.bg' in result['error']
    def fail(*args):
        raise OSError('storage unavailable')
    monkeypatch.setattr(prefs_routes, '_load_for_user', fail)
    result = asyncio.run(do_ui_control(json.dumps({'action': 'create_theme', 'name': 'Good',
        'colors': {'bg': '#fff', 'accent': '#123'}})))
    assert 'Could not save' in result['error']
    assert 'ui_event' not in result


def test_get_theme_does_not_invent_unsynchronized_client_state(monkeypatch):
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: {})
    result = asyncio.run(do_ui_control("get_theme", owner="alice"))
    assert result["theme_known"] is False
    assert "not been synchronized" in result["results"].lower()
