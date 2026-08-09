"""Fixture-only file and PowerShell vertical slices for Odysseus Cook."""

from pathlib import Path

from src.cook_ops.agents.file_inventory import run as inventory
from src.cook_ops.agents.file_move_planner import run as plan_moves
from src.cook_ops.agents.file_mover import run as move
from src.cook_ops.agents.file_verify import run as verify
from src.cook_ops.agents.powershell_executor import run as execute_powershell
from src.cook_ops.agents.powershell_preflight import run as preflight
from src.cook_ops.contracts import Operation
from src.cook_ops.controller import CookController
from src.cook_ops.skills import load_personal_skill_registry


def _contract(tmp_path: Path, operation: Operation):
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    return CookController(load_personal_skill_registry()).propose(
        operation=operation,
        source_paths=[source],
        destination_path=destination,
        allowed_roots=[tmp_path],
    )


def _approve(contract):
    return CookController(load_personal_skill_registry()).approve(
        contract, contract.contract_sha256
    )


def test_copy_transaction_is_hash_verified_and_preserves_source(tmp_path: Path):
    contract = _approve(_contract(tmp_path, Operation.COPY))
    manifest = inventory(contract)
    plan = plan_moves(contract, manifest)
    receipts = move(contract, plan)
    result = verify(contract, plan)

    assert receipts[0]["status"] == "VERIFIED"
    assert result["status"] == "VERIFIED"
    assert contract.source_paths[0].exists()
    assert (contract.destination_path / "source.txt").read_text(encoding="utf-8") == "fixture"


def test_unapproved_copy_plan_cannot_mutate_the_destination(tmp_path: Path):
    contract = _contract(tmp_path, Operation.COPY)
    manifest = inventory(contract)
    plan = plan_moves(contract, manifest)

    receipts = move(contract, plan)

    assert receipts[0]["status"] == "BLOCKED"
    assert contract.destination_path is not None
    assert not (contract.destination_path / "source.txt").exists()


def test_different_content_collision_blocks_before_move(tmp_path: Path):
    contract = _contract(tmp_path, Operation.COPY)
    (contract.destination_path / "source.txt").write_text("different", encoding="utf-8")

    manifest = inventory(contract)
    plan = plan_moves(contract, manifest)

    assert plan["status"] == "BLOCKED"
    assert contract.source_paths[0].exists()


def test_powershell_preflight_rejects_dangerous_or_invalid_script(tmp_path: Path):
    contract = _contract(tmp_path, Operation.POWERSHELL)

    invalid = preflight(contract, "Write-Output 'unterminated")
    forbidden = preflight(contract, "New-Service -Name bad -BinaryPathName x")

    assert invalid["status"] == "BLOCKED"
    assert forbidden["status"] == "BLOCKED"


def test_unapproved_powershell_contract_cannot_execute(tmp_path: Path):
    contract = _contract(tmp_path, Operation.POWERSHELL)
    script = "Write-Output 'safe'"

    result = execute_powershell(contract, script, "0" * 64)

    assert result["status"] == "BLOCKED"
