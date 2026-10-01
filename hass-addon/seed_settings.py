#!/usr/bin/env python3
"""Apply add-on search settings into Odysseus's settings.json.

The search *provider* is an application setting (not an environment variable),
so the add-on writes it, plus an optional SearXNG/custom URL, into the raw
settings file. Only the keys we own are touched; everything else is preserved.
Provider API keys are passed as environment variables instead (see run.sh), so
no plaintext secret is written here.

No app imports: this runs before the server starts and must not initialise
caches or the database.
"""

import json
import os
import sys


def _settings_path() -> str:
    explicit = os.environ.get("ODYSSEUS_SETTINGS_FILE")
    if explicit:
        return explicit
    data_dir = os.environ.get("ODYSSEUS_DATA_DIR") or "/data"
    return os.path.join(data_dir, "settings.json")


def main() -> int:
    provider = (os.environ.get("ODYSSEUS_SEED_SEARCH_PROVIDER") or "").strip().lower()
    url = (os.environ.get("ODYSSEUS_SEED_SEARCH_URL") or "").strip()
    if provider in ("", "none") and not url:
        return 0

    path = _settings_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            settings = json.load(fh)
        if not isinstance(settings, dict):
            settings = {}
    except (FileNotFoundError, ValueError, OSError):
        settings = {}

    changed = []
    if provider and provider != "none":
        settings["search_provider"] = provider
        changed.append(f"search_provider={provider}")
    if url:
        settings["search_url"] = url
        changed.append("search_url")
    if not changed:
        return 0

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        print(f"[odysseus] failed to write search settings: {exc}", file=sys.stderr)
        return 1

    print("[odysseus] applied search settings: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())