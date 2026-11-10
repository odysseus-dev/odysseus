"""Authored ranking inputs; factual identifiers from regression tests are selectors.

All sizes, dates and capabilities are synthetic, not statements about Hub models.
No copied production-catalog rows or descriptions are included.

The fixture is opt-in: a module imports ``publication_catalog`` and requests it
explicitly (``pytestmark = pytest.mark.usefixtures("publication_catalog")`` or
a test argument). It yields the authored input rows so tests can derive their
expectations from the input instead of restating it.
"""
import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures/hwfit_publication_models.json"


def authored_rows():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def publication_catalog(monkeypatch):
    from services.hwfit import models, hf_discovery

    rows = authored_rows()
    # Exercise the real merge/normalization path, independent of user caches.
    monkeypatch.setattr(models, "_models_cache", None)
    monkeypatch.setattr(models, "_load_model_file", lambda path: rows if str(path) == models.model_catalog_path() else [])
    monkeypatch.setattr(hf_discovery, "load_cached_hf_collection_models", lambda: [])
    monkeypatch.setattr(hf_discovery, "load_cached_mlx_community_models", lambda: [])
    yield authored_rows()
