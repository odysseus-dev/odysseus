"""D&D 5e SRD JSON bundles for wizard Character tab."""

from __future__ import annotations

import json
import os
from typing import Any

from titan.fugassa.paths import DND5E_DIR

ALLOWED = frozenset({
    "index",
    "classes",
    "races",
    "subclasses",
    "ability_scores",
    "skills",
    "spells",
    "features",
    "traits",
    "feats",
})


def load_resource(name: str) -> dict[str, Any] | list[Any]:
    if name not in ALLOWED:
        raise KeyError(name)
    filename = f"{name.replace('-', '_')}.json"
    path = os.path.join(DND5E_DIR, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)
