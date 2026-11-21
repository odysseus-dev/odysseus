from src.memory import MemoryManager


def test_project_search_prioritizes_relevant_memories(tmp_path):
    manager = MemoryManager(str(tmp_path))
    memories = [
        {
            "id": "lighthouse",
            "category": "fact",
            "text": "Project name: Project Lighthouse. Reference color: Teal.",
        },
        {
            "id": "relay",
            "category": "project",
            "text": "Working on a project named relay-dashboard.",
        },
        {
            "id": "drive",
            "category": "fact",
            "text": "The user's external hard drive is named ExtBackup and is located at H:.",
        },
        {
            "id": "android",
            "category": "project",
            "text": "User runs a Python project named relay-dashboard on Android.",
        },
    ]

    results = manager.get_relevant_memories(
        "Project Lighthouse",
        memories,
        threshold=0.05,
        max_items=20,
    )
    result_ids = [memory["id"] for memory in results]

    assert "lighthouse" in result_ids
    assert "relay" not in result_ids
    assert "drive" not in result_ids
    assert "android" not in result_ids


def test_reference_color_search_finds_lighthouse(tmp_path):
    manager = MemoryManager(str(tmp_path))
    memories = [
        {
            "id": "lighthouse",
            "category": "fact",
            "text": "Project name: Project Lighthouse. Reference color: Teal.",
        },
        {
            "id": "drive",
            "category": "fact",
            "text": "The user's external hard drive is named ExtBackup and is located at H:.",
        },
    ]

    results = manager.get_relevant_memories(
        "Project Lighthouse reference color teal",
        memories,
        threshold=0.05,
        max_items=20,
    )

    assert any(memory["id"] == "lighthouse" for memory in results)
