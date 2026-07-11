# Attachment References and Upload Storage

Odysseus stores uploaded bytes once under the configured upload directory and
passes stable references through chat history, tools, and future artifact work.
The goal is to avoid duplicating large inline media payloads in
`chat_messages.content` or the SQLite FTS index.

## Reference Shape

Attachment references use this minimum shape:

```json
{
  "type": "attachment_ref",
  "attachment_id": "32hex-or-32hex.ext",
  "name": "original-filename.png",
  "mime": "image/png",
  "size": 12345,
  "checksum_sha256": "hex-digest",
  "created_at": "2026-07-09T12:00:00"
}
```

Optional fields such as `width`, `height`, `vision`, `vision_model`, and
`gallery_id` may be present when the uploader or preprocessing path knows them.

## Persistence

The live model call may still receive provider-specific multimodal blocks for
the current turn. Persistence is different:

- `chat_messages.content` stores readable text plus compact attachment reference
  lines, never raw `data:*;base64,...` upload bytes.
- `chat_messages.metadata.attachments` stores structured attachment reference
  metadata for UI reloads and future processing.
- The SQLite FTS migration recreates chat-message FTS triggers so new rows do
  not index inline media payloads, and it scrubs legacy rows that were already
  indexed with data URLs.

## Tool Access

Agent/tool context receives upload entries as `attachment_ref` manifests with an
`odysseus://attachment/<id>` URI and `read_policy: "owner_checked_upload"`.

For compatibility with existing built-in tools, a local `path` may be included
only after all of these checks pass:

- the upload ID resolves through `UploadHandler.resolve_upload`;
- the requested owner is allowed to read the upload;
- the file remains inside the configured upload directory;
- the file path is inside the tool-readable roots.

External MCP/custom tools should treat the URI and attachment ID as the stable
contract and request bytes through an owner-checked server path, not by assuming
host filesystem layout.

## Retention and Deletion

Current retention behavior is conservative:

- uploads are indexed in `uploads.json` with owner, checksum, MIME type, size,
  and creation time;
- admin cleanup first scans persisted chat metadata/content, document versions,
  PDF source markers, gallery hashes, notes, and calendar records for live
  references;
- cleanup fails closed if that reference scan cannot complete, and the lower-level
  cleanup API removes nothing unless it receives a complete reference snapshot;
- expired, unreferenced uploads are removed during the completed scan, while
  attachment-bearing writers must first take an owner-checked reservation that
  serializes with deletion and refreshes the upload's access timestamp;
- deliberate removal atomically drops matching `uploads.json` rows before deleting
  the bytes and restores those rows if filesystem removal fails;
- deleting a chat removes the chat rows but does not immediately delete shared
  upload bytes, because the same upload may also be referenced by gallery items,
  documents, duplicate-upload rows, or future artifact records.

There is no distinct artifact table in the current schema. Artifact-like upload
references persisted in chat or document text are covered by the canonical
attachment-ID scan; any future artifact store must be added to reference discovery
before cleanup is allowed to consider its uploads unreferenced.

Cleanup and write reservations share the upload-index lock. This closes the
scan/write/delete race in the documented single-worker deployment; a future
multi-process deployment must add an inter-process lock or move lifecycle state
into the database before enabling destructive cleanup in more than one worker.
