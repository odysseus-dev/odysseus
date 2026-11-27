"""Tests puros del DAG: validación, capas y contexto de dependencias."""

import pytest

from orchestrator.dag import (
    ATTACHMENTS_DIR,
    DagError,
    build_task_messages,
    graph_layers,
    read_attachment,
    save_attachment,
    validate_tasks,
)


def test_order_is_topological():
    tasks = [
        {"id": "final", "prompt": "cierra", "depends_on": ["b", "a"]},
        {"id": "a", "prompt": "abre"},
        {"id": "b", "prompt": "medio", "depends_on": ["a"]},
    ]
    assert validate_tasks(tasks) == ["a", "b", "final"]


def test_cycle_is_rejected():
    tasks = [
        {"id": "a", "prompt": "a", "depends_on": ["b"]},
        {"id": "b", "prompt": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(DagError, match="ciclo"):
        validate_tasks(tasks)


def test_missing_dependency_is_rejected():
    with pytest.raises(DagError, match="no existe"):
        validate_tasks([{"id": "a", "prompt": "a", "depends_on": ["nope"]}])


def test_duplicate_ids_are_rejected():
    with pytest.raises(DagError, match="duplicados"):
        validate_tasks([
            {"id": "a", "prompt": "uno"},
            {"id": "a", "prompt": "dos"},
        ])


def test_layers_group_independent_tasks():
    tasks = [
        {"id": "a", "prompt": "a"},
        {"id": "b", "prompt": "b"},
        {"id": "c", "prompt": "c", "depends_on": ["a", "b"]},
    ]
    order = validate_tasks(tasks)
    assert graph_layers(tasks, order) == [["a", "b"], ["c"]]


def test_dependency_output_is_fed_into_the_prompt():
    run = {
        "system": "sistema",
        "tasks": {
            "a": {"id": "a", "output": "hechos", "depends_on": []},
            "b": {"id": "b", "prompt": "b", "output": "", "depends_on": ["a"]},
        },
    }
    messages = build_task_messages(run, run["tasks"]["b"])
    assert messages[0] == {"role": "system", "content": "sistema"}
    assert "hechos" in messages[1]["content"]
    assert "TAREA: b" in messages[1]["content"]


def test_attachment_is_injected_as_data(tmp_path):
    ref = save_attachment("nota.txt", "dato de prueba", "alice")
    try:
        run = {
            "owner": "alice",
            "system": "sistema",
            "tasks": {
                "a": {
                    "id": "a",
                    "prompt": "usa el adjunto",
                    "depends_on": [],
                    "attachments": [ref],
                },
            },
        }
        messages = build_task_messages(run, run["tasks"]["a"])
        content = messages[1]["content"]
        assert "ADJUNTOS" in content
        assert "dato de prueba" in content
    finally:
        (ATTACHMENTS_DIR / f"{ref['id']}.json").unlink(missing_ok=True)


def test_attachment_owner_is_enforced():
    ref = save_attachment("privado.txt", "secreto", "alice")
    try:
        assert read_attachment(ref["id"], "alice") is not None
        assert read_attachment(ref["id"], "bob") is None
        assert read_attachment(ref["id"], None) is None
    finally:
        (ATTACHMENTS_DIR / f"{ref['id']}.json").unlink(missing_ok=True)
