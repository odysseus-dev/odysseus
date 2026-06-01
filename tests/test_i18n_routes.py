import json

import pytest

import routes.i18n_routes as i18n_routes


def test_normalize_locale_code_accepts_common_shapes():
    assert i18n_routes._normalize_locale_code("EN") == "en"
    assert i18n_routes._normalize_locale_code("pt_BR") == "pt-br"
    assert i18n_routes._normalize_locale_code("zh-Hans") == "zh-hans"


@pytest.mark.parametrize("code", ["../ru", "r", "ru.json", "en/ru", "en..ru", ""])
def test_normalize_locale_code_rejects_unsafe_values(code):
    with pytest.raises(ValueError):
        i18n_routes._normalize_locale_code(code)


def test_extract_locale_code_prefers_payload_meta():
    payload = {"meta": {"locale": "RU"}}
    assert i18n_routes._extract_locale_code(payload, "en.json") == "ru"


def test_write_locale_payload_stays_inside_i18n_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n_routes, "I18N_DIR", str(tmp_path))
    info = i18n_routes._write_locale_payload(
        "ru",
        {"meta": {"name": "Russian", "nativeName": "Русский"}, "strings": {"Settings": "Настройки"}},
    )

    assert info["code"] == "ru"
    written = tmp_path / "ru.json"
    assert written.exists()
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["meta"]["locale"] == "ru"
    assert data["meta"]["name"] == "Russian"
    assert data["strings"]["Settings"] == "Настройки"


def test_list_locale_infos_ignores_invalid_files(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n_routes, "I18N_DIR", str(tmp_path))
    (tmp_path / "en.json").write_text(
        json.dumps({"meta": {"name": "English", "nativeName": "English"}}),
        encoding="utf-8",
    )
    (tmp_path / "bad.name.json").write_text("{}", encoding="utf-8")

    assert i18n_routes._list_locale_infos() == [
        {"code": "en", "name": "English", "nativeName": "English", "url": "/static/i18n/en.json"}
    ]
