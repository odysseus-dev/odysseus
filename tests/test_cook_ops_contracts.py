"""Behavioral contract tests for the bounded Odysseus Cook controller."""

from pathlib import Path

import pytest

from src.cook_ops.contracts import Operation, TaskContract, TaskStatus
from src.cook_ops.controller import CookController
from src.cook_ops.skills import load_personal_skill_registry


def test_registry_exposes_five_curated_personal_skills_with_hashes():
    registry = load_personal_skill_registry()

    assert len(registry) == 5
    assert {skill.skill_id for skill in registry} == {
        "directory-management-protocol",
        "verify-and-backup-archive-files",
        "powershell-audit-pre-flight-creation",
        "safe-haven-creation-recovery",
        "odx-backup-file-verification",
    }
    assert all(len(skill.sha256) == 64 for skill in registry)
    assert all(skill.allowed_roles for skill in registry)


def test_proposed_copy_contract_is_hash_bound_and_not_approved_by_default(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("fixture", encoding="utf-8")
    destination.mkdir()

    controller = CookController(skill_registry=load_personal_skill_registry())
    contract = controller.propose(
        operation=Operation.COPY,
        source_paths=[source],
        destination_path=destination,
        allowed_roots=[tmp_path],
        skill_ids=["verify-and-backup-archive-files"],
    )

    assert contract.status is TaskStatus.PROPOSED
    assert contract.contract_sha256
    assert not contract.approved
    assert contract.skill_bindings[0].skill_id == "verify-and-backup-archive-files"


def test_approval_requires_the_current_contract_hash(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("fixture", encoding="utf-8")
    destination.mkdir()
    controller = CookController(skill_registry=load_personal_skill_registry())
    contract = controller.propose(
        operation=Operation.COPY,
        source_paths=[source],
        destination_path=destination,
        allowed_roots=[tmp_path],
    )

    with pytest.raises(ValueError, match="hash"):
        controller.approve(contract, "0" * 64)

    approved = controller.approve(contract, contract.contract_sha256)
    assert approved.approved
    assert approved.status is TaskStatus.READY_FOR_APPROVAL


def test_contract_rejects_a_source_outside_its_allowed_root(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    controller = CookController(skill_registry=load_personal_skill_registry())

    with pytest.raises(ValueError, match="allowed roots"):
        controller.propose(
            operation=Operation.COPY,
            source_paths=[source],
            destination_path=destination,
            allowed_roots=[tmp_path / "different-root"],
        )


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        (Path(r"G:\AIW\00_IN\incoming.txt"), Path(r"G:\AIW\04_OUT\safe-destination")),
        (Path(r"G:\AIW\04_OUT\safe-source.txt"), Path(r"G:\AIW\05_BAK\archived.txt")),
    ],
)
def test_proposal_rejects_protected_zone_paths_without_an_exact_exception(
    source: Path, destination: Path
):
    controller = CookController(skill_registry=load_personal_skill_registry())

    with pytest.raises(ValueError, match="protected zone"):
        controller.propose(
            operation=Operation.COPY,
            source_paths=[source],
            destination_path=destination,
            allowed_roots=[Path(r"G:\AIW")],
        )


def test_protected_zone_exception_must_match_exact_path_and_bind_to_approval_hash():
    controller = CookController(skill_registry=load_personal_skill_registry())
    source = Path(r"G:\AIW\00_IN\incoming.txt")
    destination = Path(r"G:\AIW\04_OUT\safe-destination")

    with pytest.raises(ValueError, match="protected zone"):
        controller.propose(
            operation=Operation.COPY,
            source_paths=[source],
            destination_path=destination,
            allowed_roots=[Path(r"G:\AIW")],
            protected_zone_exceptions=[Path(r"G:\AIW\00_IN")],
        )

    contract = controller.propose(
        operation=Operation.COPY,
        source_paths=[source],
        destination_path=destination,
        allowed_roots=[Path(r"G:\AIW")],
        protected_zone_exceptions=[source],
    )

    assert contract.status is TaskStatus.PROPOSED
    assert contract.protected_zone_exceptions == (source.resolve(),)
    assert contract.canonical_payload()["protected_zone_exceptions"] == [str(source.resolve())]
    with pytest.raises(ValueError, match="hash"):
        controller.approve(contract, "0" * 64)
    assert controller.approve(contract, contract.contract_sha256).approved
