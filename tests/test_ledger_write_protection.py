"""R-4: the append-only ledger subtree is readable but not writable via file tools.

The ledger (data/ledger/*.ledger.db) is a hash-chained, tamper-evident store. Its
only legitimate writer is the append-only propose() path (src/deterministic_db.py /
host bridge). A direct write_file/edit_file would corrupt the chain. These tests pin
that writes are blocked while reads stay allowed, and normal data/ paths are
unaffected. See docs/security/agent-tool-attack-surface.md (R-4).
"""

import os
import pytest

from src.constants import DATA_DIR
from src.tool_execution import (
    _is_write_protected_path,
    _resolve_tool_path,
)

_LEDGER_DB = os.path.join(os.path.realpath(DATA_DIR), "ledger", "health.ledger.db")
_LEDGER_DIR = os.path.join(os.path.realpath(DATA_DIR), "ledger")
_NORMAL = os.path.join(os.path.realpath(DATA_DIR), "scratch_note.txt")


def test_ledger_db_is_write_protected():
    assert _is_write_protected_path(_LEDGER_DB) is True


def test_ledger_dir_itself_is_write_protected():
    assert _is_write_protected_path(_LEDGER_DIR) is True


def test_normal_data_path_is_not_write_protected():
    assert _is_write_protected_path(_NORMAL) is False


def test_write_to_ledger_is_blocked():
    with pytest.raises(ValueError) as e:
        _resolve_tool_path(_LEDGER_DB, for_write=True)
    assert "append-only" in str(e.value)


def test_read_of_ledger_is_allowed():
    # for_write defaults False — reads must still resolve (agent inspects health data).
    resolved = _resolve_tool_path(_LEDGER_DB, for_write=False)
    assert resolved == _LEDGER_DB


def test_normal_data_write_still_allowed():
    resolved = _resolve_tool_path(_NORMAL, for_write=True)
    assert resolved == _NORMAL


def test_ledger_dir_override_is_honored(monkeypatch, tmp_path):
    alt = tmp_path / "altledger"
    alt.mkdir()
    monkeypatch.setenv("LEDGER_DIR", str(alt))
    target = str(alt / "finance.ledger.db")
    assert _is_write_protected_path(os.path.realpath(target)) is True
