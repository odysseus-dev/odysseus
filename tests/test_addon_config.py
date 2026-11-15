"""Validate the Home Assistant add-on manifest against the Supervisor rules.

These rules are easy to get subtly wrong (a bare ``mqtt`` service, a bad
watchdog/webui URL, an options key missing from the schema) and a single invalid
field makes Supervisor treat the whole add-on as invalid and hide it from the
store. Mirroring the checks here catches that before it reaches Home Assistant.
"""

import os
import re
import importlib.util
import json
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE / "hass-addon" / "config.yaml"
REPO_PATH = BASE / "repository.yaml"
DB_URL_PATH = BASE / "hass-addon" / "db_url.py"
SEED_SETTINGS_PATH = BASE / "hass-addon" / "seed_settings.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# From supervisor/apps/validate.py
RE_SERVICE = re.compile(r"^(?P<service>mqtt|mysql):(?P<rights>provide|want|need)$")
RE_WATCHDOG = re.compile(r"^(?:https?|\[PROTO:\w+\]|tcp):\/\/\[HOST\]:(\[PORT:\d+\]|\d+).*$")
RE_WEBUI = re.compile(r"^(?:https?|\[PROTO:\w+\]):\/\/\[HOST\]:\[PORT:\d+\].*$")
RE_SLUG = re.compile(r"^[-a-z0-9_]+$")
RE_VERSION = re.compile(r"^[\d]+(?:\.[\d]+)*(?:-[\w.]+)?$")
ALLOWED_ARCH = {"amd64", "aarch64", "armv7", "armhf", "i386"}
MAP_TYPES = {
    "share", "media", "ssl", "config", "homeassistant_config",
    "app_config", "addon_config", "all_app_configs", "all_addon_configs",
    "addons", "backup", "data", "local_apps",
}


def _config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_repository_manifest_is_valid():
    with open(REPO_PATH, encoding="utf-8") as fh:
        repo = yaml.safe_load(fh)
    assert repo.get("name")
    assert repo.get("url", "").startswith("http")


def test_required_fields_present():
    cfg = _config()
    for key in ("name", "version", "slug", "description", "arch"):
        assert cfg.get(key), f"missing required key: {key}"
    assert RE_SLUG.match(cfg["slug"]), cfg["slug"]
    assert RE_VERSION.match(str(cfg["version"])), cfg["version"]
    assert cfg["arch"] and set(cfg["arch"]) <= ALLOWED_ARCH


def test_services_use_rights_suffix():
    # A bare "mqtt" fails RE_SERVICE and invalidates the entire add-on.
    cfg = _config()
    for service in cfg.get("services", []):
        assert RE_SERVICE.match(service), f"invalid service entry: {service!r}"


def test_watchdog_and_webui_urls_match_supervisor_regex():
    cfg = _config()
    assert RE_WATCHDOG.match(cfg["watchdog"]), cfg["watchdog"]
    assert RE_WEBUI.match(cfg["webui"]), cfg["webui"]


def test_map_entries_are_typed():
    cfg = _config()
    for entry in cfg.get("map", []):
        assert isinstance(entry, dict), entry
        assert entry.get("type") in MAP_TYPES, entry
        assert isinstance(entry.get("read_only", True), bool)


def test_options_match_schema():
    cfg = _config()
    options = cfg.get("options", {})
    schema = cfg.get("schema", {})
    assert isinstance(options, dict) and isinstance(schema, dict)
    # Every concrete option must be described by the schema.
    assert set(options) <= set(schema)
    # Every required (non-"?" / non-list-optional) schema key must have a value.
    for key, kind in schema.items():
        optional = isinstance(kind, str) and kind.endswith("?")
        if not optional:
            assert key in options, f"schema key without default: {key}"


def test_ingress_panel_fields_when_enabled():
    cfg = _config()
    if not cfg.get("ingress"):
        return  # ingress/panel is optional; only validate it when present
    assert isinstance(cfg["ingress_port"], int) and cfg["ingress_port"] > 0
    assert cfg.get("panel_icon", "").startswith("mdi:")
    assert cfg.get("panel_title")


# ---------------------------------------------------------------------------
# PostgreSQL URL builder
# ---------------------------------------------------------------------------

def test_db_url_builder_percent_encodes_credentials():
    db_url = _load(DB_URL_PATH, "addon_db_url")
    url = db_url.build_database_url(
        host="db.local", port="5433", name="ody", user="user", password="p@ss/wo:rd",
    )
    assert url == "postgresql+psycopg2://user:p%40ss%2Fwo%3Ard@db.local:5433/ody"


def test_db_url_builder_defaults_and_no_auth():
    db_url = _load(DB_URL_PATH, "addon_db_url")
    assert db_url.build_database_url(host="") == "postgresql+psycopg2://localhost:5432/odysseus"
    assert db_url.build_database_url(host="h", user="u") == "postgresql+psycopg2://u@h:5432/odysseus"


# ---------------------------------------------------------------------------
# Search settings seeder
# ---------------------------------------------------------------------------

def _run_seed_settings(tmp_path, provider, url):
    module = _load(SEED_SETTINGS_PATH, "addon_seed_settings")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"keep_me": True}), encoding="utf-8")
    os.environ["ODYSSEUS_SETTINGS_FILE"] = str(settings_file)
    os.environ["ODYSSEUS_SEED_SEARCH_PROVIDER"] = provider
    os.environ["ODYSSEUS_SEED_SEARCH_URL"] = url
    try:
        assert module.main() == 0
    finally:
        for key in ("ODYSSEUS_SETTINGS_FILE", "ODYSSEUS_SEED_SEARCH_PROVIDER", "ODYSSEUS_SEED_SEARCH_URL"):
            os.environ.pop(key, None)
    return json.loads(settings_file.read_text(encoding="utf-8"))


def test_seed_settings_writes_provider_and_url(tmp_path):
    data = _run_seed_settings(tmp_path, "brave", "http://searxng:8080")
    assert data["search_provider"] == "brave"
    assert data["search_url"] == "http://searxng:8080"
    assert data["keep_me"] is True  # unrelated keys preserved


def test_seed_settings_none_is_noop(tmp_path):
    data = _run_seed_settings(tmp_path, "none", "")
    assert "search_provider" not in data
    assert "search_url" not in data
    assert data["keep_me"] is True