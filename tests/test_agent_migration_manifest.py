import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "agent_migration_manifest.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agent_migration_manifest", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_memory_json_accepts_strings_and_objects(tmp_path):
    migration = load_module()
    path = tmp_path / "memories.json"
    path.write_text(
        json.dumps(
            [
                "Pacey prefers GLM for routine coding.",
                {"text": "Odysseus runs on a self-hosted machine.", "category": "project", "source": "manual"},
                {"content": "Duplicate source keys still work.", "category": "fact"},
            ]
        ),
        encoding="utf-8",
    )

    items, warnings = migration.collect_memory_json(path, "example-agent")

    assert [item["kind"] for item in items] == ["memory", "memory", "memory"]
    assert items[0]["category"] == "fact"
    assert items[1]["category"] == "project"
    assert items[1]["source"] == "manual"
    assert warnings == []


def test_collect_memory_json_deduplicates_exact_text(tmp_path):
    migration = load_module()
    path = tmp_path / "memories.json"
    path.write_text(json.dumps(["Same memory", {"text": "Same memory"}]), encoding="utf-8")

    items, warnings = migration.collect_memory_json(path, "example-agent")

    assert len(items) == 1
    assert warnings[0].message == "skipped duplicate memory at index 1"


def test_collect_skill_dir_scans_skill_markdown(tmp_path):
    migration = load_module()
    skill_path = tmp_path / "skills" / "dev" / "git-helper" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        """---
name: git-helper
category: dev
---

## When to Use
Use for focused git checks.
""",
        encoding="utf-8",
    )

    items, warnings = migration.collect_skill_dir(tmp_path / "skills", "example-agent")

    assert len(items) == 1
    assert warnings == []
    assert items[0]["kind"] == "skill"
    assert items[0]["name"] == "git-helper"
    assert items[0]["category"] == "dev"
    assert items[0]["format"] == "SKILL.md"
    assert "## When to Use" in items[0]["content"]


def test_collect_skill_dir_skips_symlinked_skill_markdown(tmp_path):
    migration = load_module()
    outside = tmp_path / "outside.md"
    outside.write_text("private skill content", encoding="utf-8")
    skill_path = tmp_path / "skills" / "bad" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.symlink_to(outside)

    items, warnings = migration.collect_skill_dir(tmp_path / "skills", "example-agent")

    assert items == []
    assert warnings[0].message == "skipped symlinked skill file"


def test_collect_skill_dir_skips_symlinked_root(tmp_path):
    migration = load_module()
    real_skills = tmp_path / "real-skills"
    real_skills.mkdir()
    linked_skills = tmp_path / "skills"
    linked_skills.symlink_to(real_skills, target_is_directory=True)

    items, warnings = migration.collect_skill_dir(linked_skills, "example-agent")

    assert items == []
    assert warnings[0].message == "skills path is a symlink; skipped"


def test_archive_content_is_optional(tmp_path):
    migration = load_module()
    archive = tmp_path / "notes.md"
    archive.write_text("# Notes\n\nUseful context.", encoding="utf-8")

    metadata_only, _ = migration.collect_archive_paths([archive], "example-agent")
    with_content, _ = migration.collect_archive_paths([archive], "example-agent", include_content=True)

    assert metadata_only[0]["kind"] == "archive_document"
    assert "content" not in metadata_only[0]
    assert with_content[0]["content"].startswith("# Notes")


def test_archive_skips_symlinked_file(tmp_path):
    migration = load_module()
    outside = tmp_path / "outside.md"
    outside.write_text("private archive content", encoding="utf-8")
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    linked_file = archive_dir / "leak.md"
    linked_file.symlink_to(outside)

    items, warnings = migration.collect_archive_paths([archive_dir], "example-agent", include_content=True)

    assert items == []
    assert warnings[0].message == "skipped symlinked archive path"


def test_archive_skips_symlinked_root(tmp_path):
    migration = load_module()
    archive = tmp_path / "notes.md"
    archive.write_text("# Notes\n\nUseful context.", encoding="utf-8")
    linked_archive = tmp_path / "linked-notes.md"
    linked_archive.symlink_to(archive)

    items, warnings = migration.collect_archive_paths([linked_archive], "example-agent", include_content=True)

    assert items == []
    assert warnings[0].message == "archive path is a symlink; skipped"


def test_conversation_json_imports_generic_threads_metadata_only(tmp_path):
    migration = load_module()
    path = tmp_path / "conversations.json"
    path.write_text(
        json.dumps(
            {
                "conversations": [
                    {
                        "id": "thread-1",
                        "title": "Project plan",
                        "created_at": "2026-06-01T00:00:00Z",
                        "messages": [
                            {"role": "user", "content": "Can we design this?"},
                            {"role": "assistant", "content": "Yes, start with a narrow slice."},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    items, warnings = migration.collect_conversation_json(path, "example-agent")

    assert warnings == []
    assert len(items) == 1
    assert items[0]["kind"] == "conversation_thread"
    assert items[0]["title"] == "Project plan"
    assert items[0]["metadata"]["source_id"] == "thread-1"
    assert items[0]["metadata"]["message_count"] == 2
    assert items[0]["metadata"]["content_included"] is False
    assert "messages" not in items[0]


def test_conversation_json_can_embed_generic_thread_content(tmp_path):
    migration = load_module()
    path = tmp_path / "conversations.json"
    path.write_text(
        json.dumps(
            [
                {
                    "title": "Preference",
                    "messages": [
                        {"sender": "human", "content": [{"type": "text", "text": "Use terse replies."}]},
                        {"sender": "ai", "text": "Noted."},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    items, warnings = migration.collect_conversation_json(path, "example-agent", include_content=True)

    assert warnings == []
    assert items[0]["metadata"]["content_included"] is True
    assert items[0]["messages"] == [
        {"role": "user", "text": "Use terse replies."},
        {"role": "assistant", "text": "Noted."},
    ]


def test_conversation_json_imports_chatgpt_mapping_ordered_by_time(tmp_path):
    migration = load_module()
    path = tmp_path / "conversations.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "chatgpt-thread",
                    "title": "ChatGPT export",
                    "mapping": {
                        "b": {
                            "message": {
                                "id": "m2",
                                "create_time": 20,
                                "author": {"role": "assistant"},
                                "content": {"content_type": "text", "parts": ["Second"]},
                            }
                        },
                        "a": {
                            "message": {
                                "id": "m1",
                                "create_time": 10,
                                "author": {"role": "user"},
                                "content": {"content_type": "text", "parts": ["First"]},
                            }
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    items, warnings = migration.collect_conversation_json(path, "chatgpt", include_content=True)

    assert warnings == []
    assert items[0]["metadata"]["source_format"] == "chatgpt_mapping"
    assert items[0]["messages"] == [
        {"role": "user", "text": "First", "created_at": "1970-01-01T00:00:10Z", "source_id": "m1"},
        {"role": "assistant", "text": "Second", "created_at": "1970-01-01T00:00:20Z", "source_id": "m2"},
    ]


def test_conversation_content_respects_message_limit(tmp_path):
    migration = load_module()
    path = tmp_path / "conversations.json"
    path.write_text(
        json.dumps(
            [
                {
                    "title": "Long thread",
                    "messages": [
                        {"role": "user", "content": "one"},
                        {"role": "assistant", "content": "two"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    items, warnings = migration.collect_conversation_json(
        path,
        "example-agent",
        include_content=True,
        max_messages=1,
    )

    assert "messages" not in items[0]
    assert items[0]["metadata"]["content_included"] is False
    assert warnings[0].message == "skipped conversation content at index 0: over 1 messages"


def test_archive_missing_path_warns(tmp_path):
    migration = load_module()
    missing = tmp_path / "missing"

    items, warnings = migration.collect_archive_paths([missing], "example-agent")

    assert items == []
    assert warnings[0].message == "archive path does not exist"


def test_main_writes_manifest_with_conversation_thread(tmp_path):
    migration = load_module()
    conversation_path = tmp_path / "conversations.json"
    output_path = tmp_path / "manifest.json"
    conversation_path.write_text(
        json.dumps([{"title": "A thread", "messages": [{"role": "user", "content": "hello"}]}]),
        encoding="utf-8",
    )

    exit_code = migration.main(
        [
            "--source-name",
            "example-agent",
            "--conversation-json",
            str(conversation_path),
            "--output",
            str(output_path),
        ]
    )
    manifest = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert manifest["summary"]["counts_by_kind"] == {"conversation_thread": 1}
    assert manifest["items"][0]["title"] == "A thread"


def test_main_writes_manifest(tmp_path):
    migration = load_module()
    memory_path = tmp_path / "memories.json"
    output_path = tmp_path / "manifest.json"
    memory_path.write_text(json.dumps([{"text": "A useful fact", "category": "fact"}]), encoding="utf-8")

    exit_code = migration.main(
        [
            "--source-name",
            "example-agent",
            "--memory-json",
            str(memory_path),
            "--output",
            str(output_path),
        ]
    )
    manifest = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert manifest["schema_version"] == "agent-migration.v1"
    assert manifest["summary"]["counts_by_kind"] == {"memory": 1}
    assert manifest["items"][0]["text"] == "A useful fact"
