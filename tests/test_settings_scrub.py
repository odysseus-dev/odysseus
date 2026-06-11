"""Security tests for the /api/auth/settings secret scrubbing.

The /settings endpoint is auth-exempt (the frontend + the pre-login page read it
for keybinds / TTS prefs), so non-admin and unauthenticated callers receive a
*scrubbed* copy. Secrets must never leak to them — load-bearing when the app is
reachable over a Cloudflare tunnel / reverse proxy. These pin the scrub: deep
(nested), broad secret-key coverage, and no collateral damage to real prefs.

Imports the stdlib-only `src.settings_scrub` directly, so the test does not pull
in the FastAPI / auth / database import chain.
"""
from src.settings_scrub import is_secret_key, scrub_settings


def test_top_level_secrets_blanked():
    out = scrub_settings({"search_api_key": "S", "openai_api_key": "K", "smtp_password": "P"})
    assert out["search_api_key"] == "" and out["openai_api_key"] == "" and out["smtp_password"] == ""


def test_broadened_patterns_blanked():
    s = {"smtp_pass": "a", "db_pwd": "b", "oauth_client_secret": "c",
         "gh_access_token": "d", "refresh_token": "e", "x_credential": "f", "z_apikey": "g"}
    out = scrub_settings(s)
    assert all(out[k] == "" for k in s), out


def test_nested_secret_blanked():
    out = scrub_settings({"email_account": {"host": "imap", "smtp_password": "NESTED"}})
    assert out["email_account"]["host"] == "imap"        # non-secret preserved
    assert out["email_account"]["smtp_password"] == ""   # nested secret blanked


def test_secret_in_list_of_dicts_blanked():
    out = scrub_settings({"providers": [{"name": "a", "api_key": "P1"},
                                        {"name": "b", "access_token": "T2"}]})
    assert out["providers"][0]["name"] == "a"
    assert out["providers"][0]["api_key"] == ""
    assert out["providers"][1]["access_token"] == ""


def test_non_secret_keys_preserved():
    s = {"keybinds": {"send": "Enter"}, "theme": "dark", "image_model": "x",
         "default_endpoint_id": "ep1", "search_result_count": 5, "tts_enabled": True,
         "tokenId": "public-id", "keyId": "public-key-id"}
    assert scrub_settings(s) == s  # untouched


def test_google_pse_cx_is_public():
    assert is_secret_key("google_pse_cx") is False
    assert scrub_settings({"google_pse_cx": "cx123"})["google_pse_cx"] == "cx123"


def test_webhook_integration_handle_blanked():
    out = scrub_settings({
        "reminder_webhook_integration_id": "global-webhook",
        "reminder_webhook_payload_template": '{"content":"{{message}}"}',
    })
    assert is_secret_key("reminder_webhook_integration_id") is True
    assert out["reminder_webhook_integration_id"] == ""
    assert out["reminder_webhook_payload_template"] == '{"content":"{{message}}"}'


def test_empty_and_nonstring_secret_values_untouched():
    out = scrub_settings({"api_key": "", "feature_key": 7, "x_token": None})
    assert out["api_key"] == ""     # already empty
    assert out["feature_key"] == 7  # int not blanked (string-only)
    assert out["x_token"] is None   # None not blanked


def test_exact_name_matches():
    out = scrub_settings({"password": "p", "token": "t", "secret": "s", "apikey": "a", "key": "k"})
    assert all(v == "" for v in out.values()), out


def test_camel_case_secret_keys_blanked():
    out = scrub_settings({
        "apiKey": "api-secret",
        "accessToken": "access-secret",
        "refreshToken": "refresh-secret",
        "clientSecret": "client-secret",
        "hfToken": "hf-secret",
        "nested": {"privateKey": "private-secret"},
    })
    assert out["apiKey"] == ""
    assert out["accessToken"] == ""
    assert out["refreshToken"] == ""
    assert out["clientSecret"] == ""
    assert out["hfToken"] == ""
    assert out["nested"]["privateKey"] == ""


def test_non_object_settings_return_empty_mapping():
    assert scrub_settings(["not", "settings"]) == {}
    assert scrub_settings("not settings") == {}
