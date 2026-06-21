"""Best-effort launcher for local image-edit sidecar servers.

The desktop wrapper starts the Odysseus backend, but image/inpaint models are
separate OpenAI-compatible sidecars. This script bridges the configured local
image endpoint rows to the repo's built-in diffusion server without making the
main app startup depend on optional diffusion dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
TRUEY_DISABLE = {"0", "false", "no", "off", ""}
DEFAULT_IMAGE_EDIT_ENDPOINT_ID = "directml-inpaint"
DEFAULT_IMAGE_EDIT_ENDPOINT_NAME = "SD 1.5 Inpaint DirectML"
DEFAULT_IMAGE_EDIT_ENDPOINT_BASE_URL = "http://127.0.0.1:8102/v1"
DEFAULT_IMAGE_EDIT_MODEL_ID = "stable-diffusion-v1-5-inpainting-onnx-fp16"


@dataclass(frozen=True)
class SidecarCandidate:
    endpoint_id: str
    name: str
    base_url: str
    host: str
    port: int
    model_id: str
    model_path: Path


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    if isinstance(parsed, list):
        return [str(v).strip() for v in parsed if str(v).strip()]
    return []


def _parse_local_endpoint(base_url: str) -> tuple[str, int] | None:
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host not in LOCAL_HOSTS:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return ("127.0.0.1", int(port))


def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _looks_like_image_edit(endpoint: sqlite3.Row, models: list[str]) -> bool:
    text = " ".join(
        [
            str(endpoint["id"] or ""),
            str(endpoint["name"] or ""),
            " ".join(models),
        ]
    ).lower()
    return any(token in text for token in ("inpaint", "inpainting", "image-edit", "fill"))


def resolve_model_path(model_id: str, base_dir: Path, data_dir: Path) -> Path | None:
    raw = (model_id or "").strip().strip("\"'")
    if not raw:
        return None

    direct = Path(raw)
    candidates: list[Path] = []
    if direct.is_absolute():
        candidates.append(direct)
    else:
        candidates.extend(
            [
                base_dir / raw,
                base_dir / "models" / "image-edit" / raw,
                base_dir / "models" / raw,
                data_dir / raw,
                data_dir / "models" / "image-edit" / raw,
                data_dir / "models" / raw,
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"pragma table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _set_if_column(payload: dict[str, object], columns: set[str], key: str, value: object) -> None:
    if key in columns:
        payload[key] = value


def _ensure_builtin_image_endpoint(db_path: Path, base_dir: Path, data_dir: Path) -> bool:
    """Register the bundled/local DirectML inpaint endpoint when its model exists.

    Fresh packaged data directories do not inherit repo-local endpoint rows. If
    the built-in image-edit model is present beside the app or under the data
    directory, seed a normal ModelEndpoint row so the editor can mark it ready
    and the sidecar autostarter has something concrete to launch.
    """
    model_path = resolve_model_path(DEFAULT_IMAGE_EDIT_MODEL_ID, base_dir, data_dir)
    if model_path is None:
        return False
    if not db_path.exists():
        return False

    model_json = json.dumps([DEFAULT_IMAGE_EDIT_MODEL_ID])
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        columns = _table_columns(con, "model_endpoints")
        if not columns:
            return False

        existing = con.execute(
            """
            select *
              from model_endpoints
             where id = ?
                or base_url in (?, ?)
             order by case when id = ? then 0 else 1 end
             limit 1
            """,
            (
                DEFAULT_IMAGE_EDIT_ENDPOINT_ID,
                DEFAULT_IMAGE_EDIT_ENDPOINT_BASE_URL,
                DEFAULT_IMAGE_EDIT_ENDPOINT_BASE_URL[:-3].rstrip("/"),
                DEFAULT_IMAGE_EDIT_ENDPOINT_ID,
            ),
        ).fetchone()

        if existing is not None:
            updates: dict[str, object] = {}
            if "base_url" in columns and not str(existing["base_url"] or "").rstrip("/").endswith("/v1"):
                updates["base_url"] = DEFAULT_IMAGE_EDIT_ENDPOINT_BASE_URL
            if "name" in columns and not str(existing["name"] or "").strip():
                updates["name"] = DEFAULT_IMAGE_EDIT_ENDPOINT_NAME
            if "model_type" in columns and (existing["model_type"] or "") != "image":
                updates["model_type"] = "image"
            if "endpoint_kind" in columns and (existing["endpoint_kind"] or "") not in ("local",):
                updates["endpoint_kind"] = "local"
            if "cached_models" in columns and not _json_list(existing["cached_models"]):
                updates["cached_models"] = model_json
            if "pinned_models" in columns and DEFAULT_IMAGE_EDIT_MODEL_ID not in _json_list(existing["pinned_models"]):
                updates["pinned_models"] = json.dumps(_json_list(existing["pinned_models"]) + [DEFAULT_IMAGE_EDIT_MODEL_ID])
            if "model_refresh_mode" in columns and not str(existing["model_refresh_mode"] or "").strip():
                updates["model_refresh_mode"] = "manual"
            if "model_refresh_timeout" in columns and existing["model_refresh_timeout"] is None:
                updates["model_refresh_timeout"] = 60
            if updates and "updated_at" in columns:
                updates["updated_at"] = now

            if updates:
                set_clause = ", ".join(f"{key} = ?" for key in updates)
                con.execute(
                    f"update model_endpoints set {set_clause} where id = ?",
                    [*updates.values(), existing["id"]],
                )
                con.commit()
                print(
                    f"[image-sidecars] updated image endpoint {existing['id']} "
                    f"for {DEFAULT_IMAGE_EDIT_MODEL_ID}"
                )
                return True
            return False

        payload: dict[str, object] = {}
        _set_if_column(payload, columns, "id", DEFAULT_IMAGE_EDIT_ENDPOINT_ID)
        _set_if_column(payload, columns, "name", DEFAULT_IMAGE_EDIT_ENDPOINT_NAME)
        _set_if_column(payload, columns, "base_url", DEFAULT_IMAGE_EDIT_ENDPOINT_BASE_URL)
        _set_if_column(payload, columns, "api_key", None)
        _set_if_column(payload, columns, "is_enabled", 1)
        _set_if_column(payload, columns, "hidden_models", None)
        _set_if_column(payload, columns, "cached_models", model_json)
        _set_if_column(payload, columns, "pinned_models", model_json)
        _set_if_column(payload, columns, "model_type", "image")
        _set_if_column(payload, columns, "endpoint_kind", "local")
        _set_if_column(payload, columns, "model_refresh_mode", "manual")
        _set_if_column(payload, columns, "model_refresh_interval", None)
        _set_if_column(payload, columns, "model_refresh_timeout", 60)
        _set_if_column(payload, columns, "supports_tools", None)
        _set_if_column(payload, columns, "owner", None)
        _set_if_column(payload, columns, "provider_auth_id", None)
        _set_if_column(payload, columns, "created_at", now)
        _set_if_column(payload, columns, "updated_at", now)

        keys = list(payload)
        placeholders = ", ".join("?" for _ in keys)
        con.execute(
            f"insert into model_endpoints ({', '.join(keys)}) values ({placeholders})",
            [payload[key] for key in keys],
        )
        con.commit()
        print(
            f"[image-sidecars] registered image endpoint {DEFAULT_IMAGE_EDIT_ENDPOINT_ID} "
            f"for {model_path}"
        )
        return True
    finally:
        con.close()


def _load_candidates(db_path: Path, base_dir: Path, data_dir: Path) -> list[SidecarCandidate]:
    if not db_path.exists():
        print(f"[image-sidecars] endpoint database not found: {db_path}")
        return []

    query = """
        select id, name, base_url, is_enabled, model_type, endpoint_kind,
               cached_models, pinned_models
          from model_endpoints
         where coalesce(model_type, '') = 'image'
    """
    out: list[SidecarCandidate] = []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(query).fetchall()
    finally:
        con.close()

    for row in rows:
        if not row["is_enabled"]:
            continue
        endpoint = _parse_local_endpoint(row["base_url"] or "")
        if endpoint is None:
            continue
        host, port = endpoint
        models = _json_list(row["pinned_models"]) + _json_list(row["cached_models"])
        if not _looks_like_image_edit(row, models):
            continue
        for model_id in models:
            model_path = resolve_model_path(model_id, base_dir, data_dir)
            if model_path is not None:
                out.append(
                    SidecarCandidate(
                        endpoint_id=str(row["id"] or ""),
                        name=str(row["name"] or ""),
                        base_url=str(row["base_url"] or ""),
                        host=host,
                        port=port,
                        model_id=model_id,
                        model_path=model_path,
                    )
                )
                break
    return out


def build_diffusion_command(candidate: SidecarCandidate, python_exe: Path, base_dir: Path) -> list[str]:
    server = base_dir / "scripts" / "diffusion_server.py"
    cmd = [
        str(python_exe),
        str(server),
        "--model",
        str(candidate.model_path),
        "--port",
        str(candidate.port),
        "--host",
        candidate.host,
    ]

    model_text = f"{candidate.model_id} {candidate.model_path}".lower()
    if "onnx" in model_text:
        cmd.extend(["--backend", "onnx"])
        if os.name == "nt":
            cmd.extend(["--provider", "DmlExecutionProvider"])
        cmd.extend(["--width", "512", "--height", "512"])
    return cmd


def _display_cmd(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    import shlex

    return shlex.join(cmd)


def _start_candidate(candidate: SidecarCandidate, python_exe: Path, base_dir: Path, data_dir: Path) -> None:
    if _is_port_open(candidate.host, candidate.port):
        print(
            f"[image-sidecars] {candidate.name or candidate.endpoint_id} already has "
            f"a server on {candidate.host}:{candidate.port}"
        )
        return

    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"image-sidecar-{candidate.port}.log"
    cmd = build_diffusion_command(candidate, python_exe, base_dir)
    env = os.environ.copy()
    env.setdefault("ODYSSEUS_DATA_DIR", str(data_dir))

    flags = 0
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True

    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            "\n[{ts}] Starting image sidecar {eid} ({name})\n"
            "model: {model}\n"
            "cmd: {cmd}\n".format(
                ts=datetime.now().isoformat(timespec="seconds"),
                eid=candidate.endpoint_id,
                name=candidate.name,
                model=candidate.model_path,
                cmd=_display_cmd(cmd),
            )
        )
        subprocess.Popen(
            cmd,
            cwd=str(base_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            **kwargs,
        )
    print(
        f"[image-sidecars] starting {candidate.name or candidate.endpoint_id} "
        f"on {candidate.host}:{candidate.port}; log: {log_path}"
    )


def _db_path_from_env(data_dir: Path) -> Path:
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        return Path(database_url[len(prefix) :])
    return data_dir / "app.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--data-dir", default=os.environ.get("ODYSSEUS_DATA_DIR"))
    parser.add_argument("--db", default="")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)

    if (os.environ.get("ODYSSEUS_AUTOSTART_IMAGE_SIDECARS") or "1").strip().lower() in TRUEY_DISABLE:
        print("[image-sidecars] autostart disabled by ODYSSEUS_AUTOSTART_IMAGE_SIDECARS")
        return 0

    base_dir = Path(args.base_dir).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else (base_dir / "data").resolve()
    db_path = Path(args.db).resolve() if args.db else _db_path_from_env(data_dir).resolve()
    python_exe = Path(args.python).resolve()

    try:
        _ensure_builtin_image_endpoint(db_path, base_dir, data_dir)
        candidates = _load_candidates(db_path, base_dir, data_dir)
        if not candidates:
            print("[image-sidecars] no configured local image-edit sidecars to start")
            return 0
        for candidate in candidates:
            try:
                _start_candidate(candidate, python_exe, base_dir, data_dir)
            except Exception as exc:
                print(f"[image-sidecars] failed to start {candidate.endpoint_id}: {exc}")
        return 0
    except Exception as exc:
        print(f"[image-sidecars] skipped after startup error: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
