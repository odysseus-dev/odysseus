"""Language pack API for the browser UI."""

import json
import os
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from src.auth_helpers import require_user
from src.constants import STATIC_DIR


I18N_DIR = os.path.join(STATIC_DIR, "i18n")
MAX_LANGUAGE_PACK_BYTES = 512 * 1024
_LOCALE_RE = re.compile(r"^[a-z][a-z0-9]{1,7}(?:-[a-z0-9]{2,8}){0,3}$")


def _normalize_locale_code(raw: str) -> str:
    code = str(raw or "").strip().replace("_", "-").lower()
    if not _LOCALE_RE.fullmatch(code):
        raise ValueError("Locale code must look like en, ru, pt-br, or zh-hans.")
    return code


def _safe_locale_path(code: str) -> str:
    normalized = _normalize_locale_code(code)
    base = os.path.abspath(I18N_DIR)
    path = os.path.abspath(os.path.join(base, f"{normalized}.json"))
    if os.path.commonpath([base, path]) != base:
        raise ValueError("Invalid locale path.")
    return path


def _read_json_bytes(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "Language JSON must be UTF-8 encoded.") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Language JSON must be an object.")
    return payload


def _extract_locale_code(payload: dict[str, Any], filename: str | None = None) -> str:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    candidates = [
        meta.get("locale"),
        meta.get("code"),
        payload.get("locale"),
        payload.get("code"),
        os.path.splitext(os.path.basename(filename or ""))[0],
    ]
    for candidate in candidates:
        if candidate:
            return _normalize_locale_code(str(candidate))
    raise ValueError("Language JSON needs meta.locale, meta.code, or a safe filename.")


def _locale_info_from_payload(code: str, payload: dict[str, Any]) -> dict[str, str]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    name = str(meta.get("name") or code)
    native_name = str(meta.get("nativeName") or meta.get("native_name") or name)
    return {
        "code": code,
        "name": name,
        "nativeName": native_name,
        "url": f"/static/i18n/{code}.json",
    }


def _load_locale_payload(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "rb") as f:
            payload = json.loads(f.read().decode("utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_locale_payload(code: str, payload: dict[str, Any]) -> dict[str, str]:
    path = _safe_locale_path(code)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    clean_payload = dict(payload)
    clean_meta = dict(meta)
    clean_meta["locale"] = code
    clean_meta.setdefault("code", code)
    clean_payload["meta"] = clean_meta

    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean_payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return _locale_info_from_payload(code, clean_payload)


def _list_locale_infos() -> list[dict[str, str]]:
    if not os.path.isdir(I18N_DIR):
        return []
    locales: list[dict[str, str]] = []
    for name in os.listdir(I18N_DIR):
        if not name.lower().endswith(".json"):
            continue
        stem = os.path.splitext(name)[0]
        try:
            code = _normalize_locale_code(stem)
        except ValueError:
            continue
        path = _safe_locale_path(code)
        payload = _load_locale_payload(path)
        if payload is None:
            continue
        locales.append(_locale_info_from_payload(code, payload))
    return sorted(locales, key=lambda item: (item["code"] != "en", item["nativeName"].lower(), item["code"]))


def setup_i18n_routes() -> APIRouter:
    router = APIRouter(prefix="/api/i18n", tags=["i18n"])

    @router.get("/locales")
    async def list_locales(request: Request):
        require_user(request)
        return {"locales": _list_locale_infos()}

    @router.post("/locales")
    async def upload_locale(request: Request, file: UploadFile = File(...)):
        require_user(request)
        filename = file.filename or ""
        if not filename.lower().endswith(".json"):
            raise HTTPException(400, "Upload a .json language file.")

        raw = await file.read(MAX_LANGUAGE_PACK_BYTES + 1)
        if len(raw) > MAX_LANGUAGE_PACK_BYTES:
            raise HTTPException(413, "Language JSON is too large.")

        payload = _read_json_bytes(raw)
        try:
            code = _extract_locale_code(payload, filename)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        locale = _write_locale_payload(code, payload)
        return {"ok": True, "locale": locale, "locales": _list_locale_infos()}

    return router
