from __future__ import annotations

from typing import Any

from odysseus_desktop_backend.storage import Database


class SettingsService:
    def __init__(self, db: Database):
        self.db = db

    def get(self) -> dict[str, Any]:
        return self.db.get_settings()

    def set(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("settings values must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ValueError("setting keys must be non-empty strings")
            self.db.set_setting(key, value)
        return self.get()
