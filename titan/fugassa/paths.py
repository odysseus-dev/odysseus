"""Fugassa data paths (independent from Odysseus DB)."""

from __future__ import annotations

import os

from src.constants import DATA_DIR

FUGASSA_ROOT = os.path.join(DATA_DIR, "fugassa")
SAVES_DIR = os.path.join(FUGASSA_ROOT, "saves")
CHROMA_DIR = os.path.join(FUGASSA_ROOT, "chroma")  # legacy — ADR: do not use
CONFIG_PATH = os.path.join(FUGASSA_ROOT, "config.json")
WIZARD_DRAFT_PATH = os.path.join(FUGASSA_ROOT, "wizard_draft.json")
SESSION_MANIFEST_PATH = os.path.join(FUGASSA_ROOT, "session_manifest.json")
DND5E_DIR = os.path.join(FUGASSA_ROOT, "dnd5e")
GM_TEMPLATES_DIR = os.path.join(FUGASSA_ROOT, "gm_templates")

GENERATED_SUBDIRS = ("portraits", "scenes")


def generated_dir(save_dir: str) -> str:
    return os.path.join(save_dir, "generated")


def autosave_prev_dir(save_dir: str) -> str:
    return os.path.join(save_dir, "autosave_prev")


def ensure_save_dirs(save_dir: str) -> None:
    """Create gm/, generated/{portraits,scenes}/, autosave_prev/ per ADR §L."""
    os.makedirs(os.path.join(save_dir, "gm"), exist_ok=True)
    os.makedirs(autosave_prev_dir(save_dir), exist_ok=True)
    gen = generated_dir(save_dir)
    os.makedirs(gen, exist_ok=True)
    for sub in GENERATED_SUBDIRS:
        os.makedirs(os.path.join(gen, sub), exist_ok=True)

