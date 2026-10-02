"""Per-user Settings controls for explicit foreground model fallback."""

import json
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import routes.prefs_routes as prefs_routes
import src.foreground_model_routing as foreground_model_routing


_REPO = Path(__file__).resolve().parents[1]
_SETTINGS_SOURCE = (_REPO / "static" / "js" / "settings.js").read_text(
    encoding="utf-8"
)
_STATE_MODULE = (
    _REPO / "static" / "js" / "foregroundFallbackSettings.js"
).as_uri()


def test_default_chat_card_exposes_strict_off_fallback_controls():
    soup = BeautifulSoup(
        (_REPO / "static" / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )

    toggle = soup.find(id="set-foregroundFallbackToggle")
    editor = soup.find(id="set-foregroundFallbackEditor")

    assert toggle is not None
    assert toggle.name == "input"
    assert toggle.get("type") == "checkbox"
    assert toggle.get("aria-label") == (
        "Allow fallback when the selected model is unavailable"
    )
    assert not toggle.has_attr("checked")
    assert toggle.has_attr("disabled")
    assert editor is not None
    assert editor.has_attr("hidden")
    assert soup.find(id="set-foregroundFallbackState").get("aria-live") == "polite"
    assert soup.find(id="set-foregroundFallbacks") is not None
    assert soup.find(id="set-foregroundAddFallback") is not None
    retry = soup.find(id="set-foregroundFallbackRetry")
    assert retry is not None
    assert retry.has_attr("hidden")
    text = soup.get_text(" ", strip=True)
    assert "Allow fallback when the selected model is unavailable" in text
    assert "Loading fallback settings…" in text
    assert "Authentication, authorization, request, unsupported-model" in text
    assert "No fallback candidates configured" not in text


def test_default_chat_controls_use_only_owner_scoped_new_preferences():
    start = _SETTINGS_SOURCE.index("async function initDefaultChat()")
    end = _SETTINGS_SOURCE.index("/* ── Utility Model ── */", start)
    source = _SETTINGS_SOURCE[start:end]

    assert "fetch('/api/prefs'" in source
    assert "/api/models?background=false&foreground_fallback=true" in _SETTINGS_SOURCE
    assert "endpoints: function() { return _foregroundFallbackEndpoints; }" in source
    assert "saveForegroundPref('foreground_fallback_enabled'" in source
    assert "saveForegroundPref('foreground_model_fallbacks'" in source
    assert "allowReorder: true" in source
    assert "preserveUnavailable: true" in source
    assert "maxItems: MAX_FOREGROUND_FALLBACKS" in source
    assert "fallbackPrefsLoaded = foregroundPolicy !== null" in source
    assert "fallbackToggle.disabled = !fallbackPrefsLoaded" in source
    assert "if (fallbackToggle) fallbackToggle.disabled = true" in source
    assert "Retry before changing this policy" in source
    assert "return null;" in source
    assert "fallbackRetry.addEventListener('click'" in source
    assert "document.activeElement === fallbackRetry" in source
    assert "_registerForegroundFallbackEndpointRefresh(function(endpoints, error)" in source
    assert "fallbackCatalogLoaded = false" in source
    assert "Failed to refresh fallback model choices" in source
    assert "fallbackWidget.setDisabled(!fallbackCatalogLoaded)" in source
    assert "fallbackWidget.setDisabled(true)" in source
    assert "fallbackWidget.setDisabled(false)" in source
    assert "focusWasInList" in source
    assert "fallbackWidget.setInitial(loadedPolicy.candidates)" in source
    assert "fallbackPreferenceWriter.reset();" in source
    assert "createForegroundPreferenceSaveQueue(" in source
    assert "fallbackPrefsLoaded = false;" in source
    assert "function selectableEps()" in _SETTINGS_SOURCE
    assert "function firstUnusedCandidate()" in _SETTINGS_SOURCE
    assert "nextForegroundFallbackCandidate(" in _SETTINGS_SOURCE
    assert "summarizeForegroundFallbackCandidateEligibility(" in _SETTINGS_SOURCE
    assert "none is currently eligible; the selected model remains strict" in source
    assert "eligibility cannot be verified from the current model catalog" in source
    assert "captureFallbackWidgetFocus(fbContainer, document.activeElement)" in _SETTINGS_SOURCE
    assert "restoreFallbackWidgetFocus(fbContainer, addBtn, focusState)" in _SETTINGS_SOURCE
    assert "'Fallback ' + (idx + 1) + ' endpoint'" in _SETTINGS_SOURCE
    assert "'Fallback ' + (idx + 1) + ' model'" in _SETTINGS_SOURCE
    assert "'Remove fallback ' + (idx + 1)" in _SETTINGS_SOURCE
    assert "' to position ' + (destination + 1)" in _SETTINGS_SOURCE
    assert "move.dataset.moveOffset = String(action.offset)" in _SETTINGS_SOURCE
    assert "if (focusTarget) focusTarget.focus()" in _SETTINGS_SOURCE
    assert "onReordered: function(event)" in source
    assert "default_model_fallbacks" not in source
    assert "/api/auth/settings" in source  # Existing default endpoint/model only.


def test_service_worker_precaches_new_module_and_bumps_cache():
    source = (_REPO / "static" / "sw.js").read_text(encoding="utf-8")

    assert "const CACHE_NAME = 'odysseus-v345';" in source
    assert "'/static/js/foregroundFallbackSettings.js'" in source


def test_foreground_fallback_editor_has_mobile_layout_and_disabled_states():
    source = (_REPO / "static" / "style.css").read_text(encoding="utf-8")

    assert ".settings-foreground-fallback-editor[hidden]" in source
    assert ".settings-fallback-remove:disabled" in source
    assert ".settings-fallback-remove:not(:disabled):hover" in source
    assert ".settings-fallback-add:not(:disabled):hover" in source
    assert "@media (max-width: 768px)" in source
    assert ".settings-foreground-fallback-block .settings-fallback-row" in source
    assert "grid-template-columns: 14px minmax(0, 1fr) minmax(0, 1fr);" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_foreground_fallback_state_ignores_legacy_and_reorders_exact_candidates():
    script = f"""
      import {{
        captureFallbackWidgetFocus,
        countEligibleForegroundFallbackCandidates,
        createForegroundPreferenceSaveQueue,
        moveForegroundFallbackCandidate,
        nextForegroundFallbackCandidate,
        normalizeForegroundFallbackModelCatalog,
        normalizeForegroundFallbackPrefs,
        restoreFallbackWidgetFocus,
        summarizeForegroundFallbackCandidateEligibility,
      }} from {json.dumps(_STATE_MODULE)};

      const legacyOnly = normalizeForegroundFallbackPrefs({{
        default_model_fallbacks: [{{endpoint_id: 'legacy', model: 'legacy-model'}}],
      }});
      const configured = normalizeForegroundFallbackPrefs({{
        foreground_fallback_enabled: true,
        foreground_model_fallbacks: [
          {{endpoint_id: 'one', model: 'model-one'}},
          {{endpoint_id: 'two', model: 'model-two'}},
          {{endpoint_id: 'one', model: 'model-one'}},
          {{endpoint_id: '', model: 'invalid'}},
          null,
        ],
        default_model_fallbacks: [{{endpoint_id: 'legacy', model: 'ignored'}}],
      }});
      const moved = moveForegroundFallbackCandidate(configured.candidates, 1, -1);
      const boundary = moveForegroundFallbackCandidate(moved, 0, -1);
      const oversized = normalizeForegroundFallbackPrefs({{
        foreground_fallback_enabled: true,
        foreground_model_fallbacks: Array.from({{length: 12}}, (_, index) => ({{
          endpoint_id: `endpoint-${{index}}`,
          model: `model-${{index}}`,
        }})),
      }});
      const catalog = normalizeForegroundFallbackModelCatalog({{items: [
        {{
          endpoint_id: 'chat',
          endpoint_name: 'Chat endpoint',
          model_type: 'llm',
          models: ['primary', 'duplicate'],
          models_extra: ['duplicate', 'extra'],
        }},
        {{
          endpoint_id: 'image',
          endpoint_name: 'Image endpoint',
          model_type: 'image',
          models: ['image-model'],
        }},
        {{endpoint_id: '', endpoint_name: 'Invalid', models: ['ignored']}},
      ]}});
      const repeatedAdds = [];
      for (let index = 0; index < 4; index += 1) {{
        const next = nextForegroundFallbackCandidate(catalog, repeatedAdds);
        if (next) repeatedAdds.push(next);
      }}
      const reloadedAdds = normalizeForegroundFallbackPrefs({{
        foreground_fallback_enabled: true,
        foreground_model_fallbacks: repeatedAdds.concat(repeatedAdds[0]),
      }});
      const effectiveCount = countEligibleForegroundFallbackCandidates(
        repeatedAdds.concat({{endpoint_id: 'stale', model: 'missing'}}),
        catalog
      );
      const staleOnlyCount = countEligibleForegroundFallbackCandidates(
        [{{endpoint_id: 'stale', model: 'missing'}}],
        catalog
      );
      const unknownEligibility = summarizeForegroundFallbackCandidateEligibility(
        [{{endpoint_id: 'offline', model: 'saved-model'}}],
        [{{
          id: 'offline',
          models: [],
          is_enabled: true,
          model_catalog_unknown: true,
          allowed_unknown_models: null,
        }}]
      );
      const restrictedUnknownEligibility = summarizeForegroundFallbackCandidateEligibility(
        [{{endpoint_id: 'offline', model: 'blocked-model'}}],
        [{{
          id: 'offline',
          models: [],
          is_enabled: true,
          model_catalog_unknown: true,
          allowed_unknown_models: ['allowed-model'],
        }}]
      );
      const knownStrictEligibility = summarizeForegroundFallbackCandidateEligibility(
        [{{endpoint_id: 'disabled', model: 'saved-model'}}],
        catalog
      );

      const preferenceWrites = [];
      let preferenceUnavailableCount = 0;
      let rejectNextPreferenceWrite = true;
      const preferenceWriter = createForegroundPreferenceSaveQueue(
        async (key, value) => {{
          preferenceWrites.push([key, value]);
          if (rejectNextPreferenceWrite) {{
            rejectNextPreferenceWrite = false;
            throw new Error('write failed');
          }}
        }},
        () => {{ preferenceUnavailableCount += 1; }}
      );
      const failedListWrite = preferenceWriter.save(
        'foreground_model_fallbacks',
        [{{endpoint_id: 'chat', model: 'primary'}}]
      );
      const blockedEnableWrite = preferenceWriter.save(
        'foreground_fallback_enabled',
        true
      );
      const failedWriteResults = await Promise.allSettled([
        failedListWrite,
        blockedEnableWrite,
      ]);
      preferenceWriter.reset();
      await preferenceWriter.save('foreground_fallback_enabled', true);

      const focusLog = [];
      function control(key) {{
        return {{
          dataset: {{fallbackFocus: key}},
          focus() {{ focusLog.push(key); }},
        }};
      }}
      function row(controls) {{
        return {{
          controls,
          querySelectorAll() {{ return this.controls; }},
        }};
      }}
      const oldModel = control('model');
      const oldRemove = control('remove');
      const firstRow = row([oldModel, oldRemove]);
      oldModel.closest = () => firstRow;
      oldRemove.closest = () => firstRow;
      const focusContainer = {{
        children: [firstRow],
        contains(active) {{ return firstRow.controls.includes(active); }},
      }};
      const modelFocus = captureFallbackWidgetFocus(focusContainer, oldModel);
      focusContainer.children = [row([control('model'), control('remove')])];
      restoreFallbackWidgetFocus(focusContainer, control('add'), modelFocus);
      const removeFocus = captureFallbackWidgetFocus(
        {{children: [firstRow], contains(active) {{ return active === oldRemove; }}}},
        oldRemove
      );
      focusContainer.children = [];
      restoreFallbackWidgetFocus(focusContainer, control('add'), removeFocus);
      console.log(JSON.stringify({{
        legacyOnly,
        configured,
        moved,
        boundary,
        oversized,
        catalog,
        repeatedAdds,
        reloadedAdds,
        effectiveCount,
        staleOnlyCount,
        unknownEligibility,
        restrictedUnknownEligibility,
        knownStrictEligibility,
        preferenceWrites,
        preferenceUnavailableCount,
        failedWriteStatuses: failedWriteResults.map(result => result.status),
        focusLog,
      }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "legacyOnly": {"enabled": False, "candidates": []},
        "configured": {
            "enabled": True,
            "candidates": [
                {"endpoint_id": "one", "model": "model-one"},
                {"endpoint_id": "two", "model": "model-two"},
            ],
        },
        "moved": [
            {"endpoint_id": "two", "model": "model-two"},
            {"endpoint_id": "one", "model": "model-one"},
        ],
        "boundary": [
            {"endpoint_id": "two", "model": "model-two"},
            {"endpoint_id": "one", "model": "model-one"},
        ],
        "oversized": {
            "enabled": True,
            "candidates": [
                {"endpoint_id": f"endpoint-{index}", "model": f"model-{index}"}
                for index in range(10)
            ],
        },
        "catalog": [
            {
                "id": "chat",
                "name": "Chat endpoint",
                "is_enabled": True,
                "models": ["primary", "duplicate", "extra"],
                "online": True,
                "model_catalog_unknown": False,
                "allowed_unknown_models": None,
            },
        ],
        "repeatedAdds": [
            {"endpoint_id": "chat", "model": "primary"},
            {"endpoint_id": "chat", "model": "duplicate"},
            {"endpoint_id": "chat", "model": "extra"},
        ],
        "reloadedAdds": {
            "enabled": True,
            "candidates": [
                {"endpoint_id": "chat", "model": "primary"},
                {"endpoint_id": "chat", "model": "duplicate"},
                {"endpoint_id": "chat", "model": "extra"},
            ],
        },
        "effectiveCount": 3,
        "staleOnlyCount": 0,
        "unknownEligibility": {
            "configured": 1,
            "eligible": 0,
            "unknown": 1,
            "ineligible": 0,
        },
        "restrictedUnknownEligibility": {
            "configured": 1,
            "eligible": 0,
            "unknown": 0,
            "ineligible": 1,
        },
        "knownStrictEligibility": {
            "configured": 1,
            "eligible": 0,
            "unknown": 0,
            "ineligible": 1,
        },
        "preferenceWrites": [
            [
                "foreground_model_fallbacks",
                [{"endpoint_id": "chat", "model": "primary"}],
            ],
            ["foreground_fallback_enabled", True],
        ],
        "preferenceUnavailableCount": 1,
        "failedWriteStatuses": ["rejected", "rejected"],
        "focusLog": ["model", "add"],
    }


def test_preferences_api_roundtrips_fallback_policy_per_user_without_legacy_copy(
    tmp_path,
    monkeypatch,
):
    prefs_file = tmp_path / "user_prefs.json"
    legacy = [{"endpoint_id": "legacy", "model": "legacy-model"}]
    prefs_file.write_text(
        json.dumps({"default_model_fallbacks": legacy}),
        encoding="utf-8",
    )
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    current = {"owner": "alice"}
    monkeypatch.setattr(
        prefs_routes,
        "get_current_user",
        lambda request: current["owner"],
    )
    app = FastAPI()
    app.include_router(prefs_routes.setup_prefs_routes())
    client = TestClient(app)
    candidates = [
        {"endpoint_id": "backup-two", "model": "model-two"},
        {"endpoint_id": "backup-one", "model": "model-one"},
    ]

    assert client.put(
        "/api/prefs/foreground_fallback_enabled",
        json={"value": True},
    ).status_code == 200
    assert client.put(
        "/api/prefs/foreground_model_fallbacks",
        json={"value": candidates},
    ).status_code == 200
    alice = client.get("/api/prefs").json()
    assert alice == {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": candidates,
    }
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: [
            ("https://backup-two.example/v1", entries[0]["model"], {}),
            ("https://backup-one.example/v1", entries[1]["model"], {}),
        ],
    )
    policy = foreground_model_routing.resolve_foreground_model_policy("alice")
    assert policy.enabled is True
    assert [candidate[1] for candidate in policy.fallback_candidates] == [
        "model-two",
        "model-one",
    ]

    current["owner"] = "bob"
    assert client.get("/api/prefs").json() == {}
    assert client.put(
        "/api/prefs/foreground_fallback_enabled",
        json={"value": False},
    ).status_code == 200
    assert client.get("/api/prefs").json() == {
        "foreground_fallback_enabled": False,
    }

    current["owner"] = "alice"
    assert client.get("/api/prefs").json()["foreground_model_fallbacks"] == candidates
    raw = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert raw["default_model_fallbacks"] == legacy
    assert "default_model_fallbacks" not in raw["_users"]["alice"]
    assert "default_model_fallbacks" not in raw["_users"]["bob"]
