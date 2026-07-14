"""Full save snapshot for undo — ADR #9 autosave_prev/."""

from __future__ import annotations

import logging
import os
import shutil
from typing import Iterable

from titan.fugassa.paths import autosave_prev_dir

LOG = logging.getLogger("titan.fugassa.snapshot")

# Copied into autosave_prev before each mutating turn.
_SNAPSHOT_ITEMS = ("game.db", "game.json", "generated", "gm")


def _clear_dir(path: str) -> None:
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            try:
                os.remove(full)
            except OSError:
                pass


def create_autosave_prev(save_dir: str) -> str:
    """Replace autosave_prev/ with a full copy of current save artifacts."""
    dest_root = autosave_prev_dir(save_dir)
    os.makedirs(dest_root, exist_ok=True)
    _clear_dir(dest_root)
    for item in _SNAPSHOT_ITEMS:
        src = os.path.join(save_dir, item)
        dst = os.path.join(dest_root, item)
        if not os.path.exists(src):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    LOG.debug("autosave_prev snapshot created at %s", dest_root)
    return dest_root


def restore_autosave_prev(save_dir: str) -> bool:
    """Restore save from autosave_prev/; return False if no snapshot."""
    src_root = autosave_prev_dir(save_dir)
    if not os.path.isdir(src_root):
        return False
    has_any = any(os.path.exists(os.path.join(src_root, item)) for item in _SNAPSHOT_ITEMS)
    if not has_any:
        return False
    for item in _SNAPSHOT_ITEMS:
        src = os.path.join(src_root, item)
        dst = os.path.join(save_dir, item)
        if not os.path.exists(src):
            continue
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        elif os.path.isfile(dst):
            os.remove(dst)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    LOG.debug("autosave_prev restored into %s", save_dir)
    return True


def has_autosave_prev(save_dir: str) -> bool:
    root = autosave_prev_dir(save_dir)
    return os.path.isfile(os.path.join(root, "game.json")) or os.path.isfile(
        os.path.join(root, "game.db")
    )


def clear_autosave_prev(save_dir: str) -> None:
    """Remove consumed undo snapshot so the player cannot undo twice."""
    _clear_dir(autosave_prev_dir(save_dir))
