import json

from src.attachment_refs import (
    attachment_ref,
    persistable_message_content,
    search_index_text,
)


def test_persistable_message_content_replaces_inline_media_with_attachment_ref():
    metadata = {
        "attachments": [
            {
                "id": "abc123.png",
                "name": "diagram.png",
                "mime": "image/png",
                "size": 42,
                "checksum_sha256": "sha256-digest",
                "created_at": "2026-07-09T12:00:00",
                "vision": "A small architecture diagram.",
            }
        ]
    }
    content = [
        {"type": "text", "text": "Please inspect this."},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + ("A" * 5000)},
        },
    ]

    stored = persistable_message_content(content, metadata)

    assert "base64" not in stored
    assert "A" * 100 not in stored
    assert "Please inspect this." in stored
    assert "Attachment: diagram.png" in stored
    assert "id=abc123.png" in stored
    assert "sha256=sha256-digest" in stored
    assert "A small architecture diagram." in stored


def test_search_index_text_strips_legacy_serialized_data_url_blocks():
    legacy = json.dumps([
        {"type": "text", "text": "Find this useful caption"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + ("B" * 4096)},
        },
    ])

    indexed = search_index_text(legacy)

    assert indexed == "Find this useful caption\n[1 inline media payload omitted]"


def test_attachment_ref_normalizes_hash_aliases():
    ref = attachment_ref({
        "id": "file-id",
        "original_name": "report.pdf",
        "mime": "application/pdf",
        "size": 99,
        "hash": "abc",
        "uploaded_at": "2026-07-09T12:00:00",
    })

    assert ref == {
        "type": "attachment_ref",
        "attachment_id": "file-id",
        "name": "report.pdf",
        "mime": "application/pdf",
        "size": 99,
        "checksum_sha256": "abc",
        "created_at": "2026-07-09T12:00:00",
    }
