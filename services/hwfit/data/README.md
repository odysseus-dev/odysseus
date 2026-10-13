# Runtime model catalogs

The shipped `hf_models.json` and `mlx_community_models.json` contain independently
authored empty JSON lists (`[]`). They contain no copied upstream rows,
descriptions, weights or metadata. A fresh offline installation has no catalog
recommendations until user data has been populated.

Use **Rescan** in Cookbook while online (the models API accepts
`refresh_catalog=1`). Existing discovery code fetches selected Hugging Face
organization collections and MLX community collections into `DATA_DIR/hwfit/`:
`hf_collection_models.json` and `mlx_community_models.json`. These runtime cache
files retain source/fetch timestamps and model rows, with the existing 24-hour
freshness policy. Forced refresh bypasses freshness, invalidates the merged
in-memory catalog, and preserves the existing cache/network failure behavior.
Previously populated caches can supply offline results; a failed cold refresh
leaves an explicit empty-state message. Refresh does not write shipped lists.

Maintenance commands from the repository root:

```
python scripts/add_hwfit_models.py
python scripts/backfill_model_release_dates.py --dry-run
python scripts/import_from_vllm_recipes.py --dry-run
```

Their catalog path is `DATA_DIR/hwfit/hf_models.json`, merged before runtime
collection caches. The add/import commands can initialize a missing runtime
catalog; backfill requires one. These commands use Hub metadata and, for recipe
import, vLLM recipe inputs. Runtime metadata is user data, not an approved
redistributable snapshot. Publishing any populated catalog requires its own
source/rights review; metadata, copied descriptions and model-weight licenses
are distinct. Do not copy runtime results into these repository lists.

Tests use `tests/fixtures/hwfit_publication_models.json` through the scoped
`tests/hwfit_publication_fixtures.py` fixture. The rows are independently authored
synthetic test inputs. No production snapshot is required to test platform,
quantization and GGUF behavior.
