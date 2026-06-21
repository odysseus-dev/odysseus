import os
import sqlite3
from pathlib import Path

from scripts import autostart_image_sidecars as sidecars


def test_resolve_model_path_finds_repo_image_edit_model(tmp_path):
    base_dir = tmp_path / "repo"
    data_dir = tmp_path / "data"
    model_dir = base_dir / "models" / "image-edit" / "stable-diffusion-v1-5-inpainting-onnx-fp16"
    model_dir.mkdir(parents=True)

    resolved = sidecars.resolve_model_path(
        "stable-diffusion-v1-5-inpainting-onnx-fp16",
        base_dir,
        data_dir,
    )

    assert resolved == model_dir.resolve()


def test_load_candidates_uses_enabled_local_image_endpoint(tmp_path):
    base_dir = tmp_path / "repo"
    data_dir = tmp_path / "data"
    model_dir = base_dir / "models" / "image-edit" / "stable-diffusion-v1-5-inpainting-onnx-fp16"
    model_dir.mkdir(parents=True)
    db_path = data_dir / "app.db"
    data_dir.mkdir()

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            create table model_endpoints (
                id text,
                name text,
                base_url text,
                is_enabled integer,
                model_type text,
                endpoint_kind text,
                cached_models text,
                pinned_models text
            )
            """
        )
        con.execute(
            """
            insert into model_endpoints values (
                'directml-inpaint',
                'SD 1.5 Inpaint DirectML',
                'http://127.0.0.1:8102/v1',
                1,
                'image',
                'local',
                '["stable-diffusion-v1-5-inpainting-onnx-fp16"]',
                '["stable-diffusion-v1-5-inpainting-onnx-fp16"]'
            )
            """
        )
        con.commit()
    finally:
        con.close()

    candidates = sidecars._load_candidates(db_path, base_dir, data_dir)

    assert len(candidates) == 1
    assert candidates[0].endpoint_id == "directml-inpaint"
    assert candidates[0].port == 8102
    assert candidates[0].model_path == model_dir.resolve()


def test_ensure_builtin_image_endpoint_seeds_fresh_runtime_db(tmp_path):
    base_dir = tmp_path / "repo"
    data_dir = tmp_path / "data"
    model_dir = base_dir / "models" / "image-edit" / "stable-diffusion-v1-5-inpainting-onnx-fp16"
    model_dir.mkdir(parents=True)
    db_path = data_dir / "app.db"
    data_dir.mkdir()

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            create table model_endpoints (
                id text,
                name text,
                base_url text,
                api_key text,
                is_enabled integer,
                hidden_models text,
                cached_models text,
                pinned_models text,
                model_type text,
                endpoint_kind text,
                model_refresh_mode text,
                model_refresh_interval integer,
                model_refresh_timeout integer,
                supports_tools integer,
                owner text,
                provider_auth_id text,
                created_at text,
                updated_at text
            )
            """
        )
        con.commit()
    finally:
        con.close()

    assert sidecars._ensure_builtin_image_endpoint(db_path, base_dir, data_dir) is True

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("select * from model_endpoints where id = 'directml-inpaint'").fetchone()
    finally:
        con.close()

    assert row is not None
    assert row["base_url"] == "http://127.0.0.1:8102/v1"
    assert row["is_enabled"] == 1
    assert row["model_type"] == "image"
    assert row["endpoint_kind"] == "local"
    assert sidecars.DEFAULT_IMAGE_EDIT_MODEL_ID in sidecars._json_list(row["cached_models"])
    assert sidecars.DEFAULT_IMAGE_EDIT_MODEL_ID in sidecars._json_list(row["pinned_models"])


def test_ensure_builtin_image_endpoint_adopts_existing_root_url(tmp_path):
    base_dir = tmp_path / "repo"
    data_dir = tmp_path / "data"
    model_dir = base_dir / "models" / "image-edit" / "stable-diffusion-v1-5-inpainting-onnx-fp16"
    model_dir.mkdir(parents=True)
    db_path = data_dir / "app.db"
    data_dir.mkdir()

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            create table model_endpoints (
                id text,
                name text,
                base_url text,
                is_enabled integer,
                model_type text,
                endpoint_kind text,
                cached_models text,
                pinned_models text
            )
            """
        )
        con.execute(
            """
            insert into model_endpoints values (
                'custom-image',
                'Existing image sidecar',
                'http://127.0.0.1:8102',
                1,
                'llm',
                'auto',
                null,
                null
            )
            """
        )
        con.commit()
    finally:
        con.close()

    assert sidecars._ensure_builtin_image_endpoint(db_path, base_dir, data_dir) is True

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("select * from model_endpoints where id = 'custom-image'").fetchone()
        count = con.execute("select count(*) from model_endpoints").fetchone()[0]
    finally:
        con.close()

    assert count == 1
    assert row["base_url"] == "http://127.0.0.1:8102/v1"
    assert row["model_type"] == "image"
    assert row["endpoint_kind"] == "local"
    assert sidecars.DEFAULT_IMAGE_EDIT_MODEL_ID in sidecars._json_list(row["cached_models"])
    assert sidecars.DEFAULT_IMAGE_EDIT_MODEL_ID in sidecars._json_list(row["pinned_models"])


def test_build_diffusion_command_adds_onnx_directml_flags_on_windows(tmp_path):
    candidate = sidecars.SidecarCandidate(
        endpoint_id="directml-inpaint",
        name="SD 1.5 Inpaint DirectML",
        base_url="http://127.0.0.1:8102/v1",
        host="127.0.0.1",
        port=8102,
        model_id="stable-diffusion-v1-5-inpainting-onnx-fp16",
        model_path=tmp_path / "stable-diffusion-v1-5-inpainting-onnx-fp16",
    )

    cmd = sidecars.build_diffusion_command(candidate, Path("python"), tmp_path)

    assert "--backend" in cmd
    assert "onnx" in cmd
    assert "--width" in cmd
    assert "512" in cmd
    if os.name == "nt":
        assert "--provider" in cmd
        assert "DmlExecutionProvider" in cmd
