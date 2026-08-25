"""Curated MLX metadata (services/hwfit/mlx_curated.py).

The HuggingFace API reports PACKED parameter counts for MLX repos — its number
comes from safetensors.total, which counts the uint32 words a 4-bit build stores
its weights in, so mlx-community/Devstral-Small-2505-4bit advertises 3.68B for a
23.6B model. There is no API that can correct this, hence the hand-curated table.
"""

import json

from services.hwfit.mlx_curated import (
    _CURATED_PATH,
    apply_curated_mlx_metadata,
    curated_mlx_entry,
    load_curated_mlx_models,
)
from services.hwfit.models import get_models


def test_curated_table_is_seeded_and_well_formed():
    table = load_curated_mlx_models()
    assert len(table) >= 10, "curated table should cover the popular mlx-community repos"
    for repo, row in table.items():
        assert repo.startswith("mlx-community/"), repo
        assert row["params_b"] > 0
        assert row["bits"] in (3, 4, 5, 6, 8)
        assert row["download_gb"] > 0
        assert row["context"] > 0


def test_curated_file_documents_why_it_is_hand_maintained():
    with open(_CURATED_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    note = " ".join(raw["_comment"]).lower()
    assert "safetensors" in note and "packed" in note


def test_unknown_repo_is_untouched():
    assert curated_mlx_entry("mlx-community/Not-A-Real-Repo-4bit") is None
    row = {"name": "mlx-community/Not-A-Real-Repo-4bit", "parameters_raw": 123, "quantization": "mlx-4bit"}
    assert apply_curated_mlx_metadata(dict(row)) == row


def test_curated_row_overrides_packed_parameter_count():
    row = apply_curated_mlx_metadata({
        "name": "mlx-community/Devstral-Small-2505-4bit",
        # What the HF API reports: the packed tensor count, ~6x too small.
        "parameters_raw": 3_683_537_920,
        "parameter_count": "3.68354B",
        "quantization": "mlx-4bit",
        "context_length": 32768,
    })
    assert row["parameters_raw"] == 23_600_000_000
    assert row["parameter_count"] == "23.6B"
    assert row["quantization"] == "mlx-4bit"
    assert row["context_length"] == 131072
    assert row["size_gb"] == 13.4
    assert row["min_ram_gb"] == 14.2
    assert row["_mlx_curated"] is True


def test_curated_metadata_reaches_the_catalog():
    """The correction has to land on the rows hwfit actually ranks, not just in
    the table — this repo ships in the bundled MLX catalog with the packed count."""
    catalog = {m["name"]: m for m in get_models()}
    row = catalog["mlx-community/Devstral-Small-2505-4bit"]
    assert row["parameters_raw"] == 23_600_000_000
    assert row["_mlx_curated"] is True


def test_non_mlx_catalog_rows_are_not_curated():
    catalog = {m["name"]: m for m in get_models()}
    assert not any(
        m.get("_mlx_curated") for name, m in catalog.items() if not name.startswith("mlx-community/")
    )


def test_apply_is_a_noop_for_non_dicts():
    assert apply_curated_mlx_metadata(None) is None
    assert apply_curated_mlx_metadata("nope") == "nope"
