"""Tests for the source-keyed locale catalogs + the validator.

Covers the real shipped catalogs (static/locales) so a broken/incomplete
translation or a malformed registry fails CI.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES = os.path.join(ROOT, "static", "locales")
sys.path.insert(0, os.path.join(ROOT, "scripts", "i18n"))

import check_locales  # noqa: E402

RESERVED = {"_meta", "_overrides"}


def _load(code):
    with open(os.path.join(LOCALES, f"{code}.json"), encoding="utf-8-sig") as f:
        return json.load(f)


def _registry():
    with open(os.path.join(LOCALES, "index.json"), encoding="utf-8-sig") as f:
        return json.load(f)


def test_validator_passes_with_no_errors():
    errors, _warnings = check_locales.check(LOCALES, strict=False)
    assert errors == [], f"catalog validation errors: {errors}"


def test_every_registered_locale_has_a_catalog():
    for loc in _registry()["locales"]:
        path = os.path.join(LOCALES, f"{loc['code']}.json")
        assert os.path.exists(path), f"missing catalog for {loc['code']}"


def test_meta_code_matches_filename():
    for loc in _registry()["locales"]:
        cat = _load(loc["code"])
        assert cat["_meta"]["code"] == loc["code"]
        for field in ("name", "nativeName", "dir"):
            assert cat["_meta"].get(field), f"{loc['code']}: _meta.{field} missing"


def test_ja_is_complete_against_canonical_en():
    en = check_locales.source_keys(_load("en"))
    ja = check_locales.source_keys(_load("ja"))
    missing = en - ja
    assert not missing, f"ja.json is missing {len(missing)} string(s): {sorted(missing)[:10]}"


def test_collision_overrides_present_and_keyed_correctly():
    ja = _load("ja")
    overrides = ja.get("_overrides", {})
    # The known genuine context-collisions resolved at build time.
    assert overrides.get("theme.saved_btn") == "保存済み"
    assert overrides.get("settings.email_form.email") == "メールアドレス"


def test_no_collision_class_in_source_keyed_model():
    # Source-keyed => the key is the English string, so one English string can
    # only ever map to one default translation. (Disambiguation lives in
    # _overrides, addressed by dotted key, not by source text.)
    ja = _load("ja")
    keys = [k for k in ja if k not in RESERVED]
    assert len(keys) == len(set(keys))
