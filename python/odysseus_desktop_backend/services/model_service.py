from __future__ import annotations

import json
import shutil
import socket
import urllib.error
import urllib.request
from typing import Any

from odysseus_desktop_backend.logging_config import get_logger
from odysseus_desktop_backend.storage import Database, utc_ms


OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
logger = get_logger("models")


class ModelService:
    def __init__(self, db: Database):
        self.db = db

    def detect_ollama(self) -> dict[str, Any]:
        installed = shutil.which("ollama") is not None
        reachable = self._tcp_reachable("127.0.0.1", 11434)
        models: list[str] = []
        version = ""
        error = ""

        if reachable:
            try:
                tags = self._get_json(f"{OLLAMA_ENDPOINT}/api/tags", timeout=3)
                models = [
                    item.get("name", "")
                    for item in tags.get("models", [])
                    if isinstance(item, dict) and item.get("name")
                ]
            except Exception as exc:  # noqa: BLE001 - surfaced in runtime status
                error = str(exc)
            try:
                version_data = self._get_json(f"{OLLAMA_ENDPOINT}/api/version", timeout=2)
                version = str(version_data.get("version", ""))
            except Exception:
                pass

        status = {
            "name": "ollama",
            "installed": installed,
            "reachable": reachable,
            "endpoint": OLLAMA_ENDPOINT,
            "version": version,
            "models": models,
            "error": error,
            "updated_at": utc_ms(),
        }
        self._store_runtime_status(status)
        logger.info(
            "ollama detection installed=%s reachable=%s endpoint=%s models=%s error=%s",
            installed,
            reachable,
            OLLAMA_ENDPOINT,
            len(models),
            error,
        )
        return status

    def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        if not model:
            raise ValueError("model is required")
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        data = self._post_json(f"{OLLAMA_ENDPOINT}/api/chat", payload, timeout=120)
        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        raise RuntimeError("Ollama returned no assistant message")

    def _store_runtime_status(self, status: dict[str, Any]) -> None:
        self.db.conn.execute(
            """
            INSERT INTO runtime_status(
                name, reachable, installed, endpoint, version, models_json, error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                reachable = excluded.reachable,
                installed = excluded.installed,
                endpoint = excluded.endpoint,
                version = excluded.version,
                models_json = excluded.models_json,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                status["name"],
                1 if status["reachable"] else 0,
                1 if status["installed"] else 0,
                status["endpoint"],
                status["version"],
                json.dumps(status["models"]),
                status["error"],
                status["updated_at"],
            ),
        )
        self.db.conn.commit()

    def _tcp_reachable(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    def _get_json(self, url: str, timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)

    def _post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama is not reachable at {OLLAMA_ENDPOINT}") from exc
        return json.loads(raw)
