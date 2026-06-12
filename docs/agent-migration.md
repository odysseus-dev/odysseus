# Agent migration manifests

Odysseus should be able to learn from another agent without blindly trusting
that agent's whole state. The safe migration path is:

```text
source agent export -> source adapter -> agent-migration.v1 manifest -> preview -> apply
```

The manifest is intentionally source-neutral. OpenClaw, Hermes, a folder of
Markdown notes, or any other agent can have its own adapter, but Odysseus only
needs to understand the normalized manifest.

## Why not import everything as memory?

Durable memory should stay compact and useful. Long notes, logs, session
transcripts, and project archives are useful context, but they are not all
memories. A good migration keeps two layers separate:

- **Archive documents** preserve source material for search, reading, and later
  extraction.
- **Memory candidates** are short facts or preferences that can be reviewed
  before being saved into Odysseus memory.

This keeps Odysseus' existing memory-review flow intact while giving it better
source material to review.

## Manifest shape

`agent-migration.v1` is a JSON object:

```json
{
  "schema_version": "agent-migration.v1",
  "generated_at": "2026-06-06T00:00:00Z",
  "source": {
    "name": "example-agent",
    "kind": "generic"
  },
  "summary": {
    "item_count": 3,
    "counts_by_kind": {
      "memory": 1,
      "skill": 1,
      "conversation_thread": 1,
      "archive_document": 1
    },
    "warning_count": 0
  },
  "items": [],
  "warnings": []
}
```

Each item has a stable `id`, a `kind`, source metadata, and enough content for a
future importer to preview it before applying.

Supported item kinds in the first pass:

- `memory` — a candidate memory with `text`, `category`, `source`, and
  provenance metadata.
- `skill` — a `SKILL.md` file with content and parsed frontmatter metadata.
- `conversation_thread` — a normalized transcript thread from an exported chat
  history. Message content is optional; adapters can preserve only thread
  metadata, message counts, timestamps, and hashes when a manifest should stay
  small or avoid embedding private transcript text.
- `archive_document` — long-form source material. Content is optional; adapters
  can preserve only path/hash/size metadata when a manifest should stay small.

## Build a manifest

Use the read-only helper:

```bash
python3 scripts/agent_migration_manifest.py \
  --source-name old-agent \
  --source-kind generic \
  --memory-json /path/to/memories.json \
  --skills-dir /path/to/skills \
  --conversation-json /path/to/conversations.json \
  --archive /path/to/notes \
  --output /tmp/agent-migration.json
```

The helper does not write to `data/`, call an LLM, import Odysseus modules, or
modify the source. It only writes JSON.

Memory JSON may be:

```json
[
  "A plain memory string",
  {
    "text": "A categorized memory",
    "category": "preference",
    "source": "old-agent"
  }
]
```

or an object containing a list under `memories`, `memory`, `items`, or `data`.

Skills are scanned recursively for `SKILL.md`:

```bash
python3 scripts/agent_migration_manifest.py \
  --source-name hermes \
  --source-kind hermes \
  --skills-dir ~/.hermes/skills \
  --output /tmp/hermes-skills-manifest.json
```

Archive documents are metadata-only by default. To embed text content:

```bash
python3 scripts/agent_migration_manifest.py \
  --source-name notes-export \
  --archive /path/to/markdown-notes \
  --include-archive-content \
  --output /tmp/notes-manifest.json
```

Conversation exports are also metadata-only by default:

```bash
python3 scripts/agent_migration_manifest.py \
  --source-name chatgpt-export \
  --source-kind chatgpt \
  --conversation-json /path/to/conversations.json \
  --output /tmp/chatgpt-conversations-manifest.json
```

The first pass supports generic conversation JSON such as:

```json
[
  {
    "id": "thread-1",
    "title": "Project plan",
    "messages": [
      {"role": "user", "content": "Can we design this?"},
      {"role": "assistant", "content": "Yes, start with a narrow slice."}
    ]
  }
]
```

It also recognizes ChatGPT-style `mapping` exports from `conversations.json`.
To embed normalized messages:

```bash
python3 scripts/agent_migration_manifest.py \
  --source-name chatgpt-export \
  --source-kind chatgpt \
  --conversation-json /path/to/conversations.json \
  --include-conversation-content \
  --max-conversation-messages 2000 \
  --output /tmp/chatgpt-conversations-with-content.json
```

Content embedding is explicit because exported chat histories can be huge and
private. A future source-specific adapter can add ZIP traversal, attachment
metadata, and provider-specific project/workspace fields while still emitting
the same `conversation_thread` manifest item.

## Recommended apply behavior

A future Odysseus importer should treat the manifest as untrusted user-provided
data and apply it in stages:

1. Show a dry-run summary with counts, warnings, duplicates, and sample items.
2. Back up current `data/` state before writing anything.
3. Import archive documents as documents or another searchable source, not as
   memory.
4. Import conversation threads as searchable archived context first, with
   citations back to the source thread. Do not turn whole transcripts into
   memory.
5. Show memory candidates for review before saving through the normal memory
   path.
6. Import skills only after name/category conflict checks.
7. Skip secrets by default. Credentials need explicit, provider-specific flows.

## What belongs in source adapters?

Adapters can be source-specific. The core manifest should not be.

For example, an OpenClaw adapter may know about OpenClaw's workspace files. A
Hermes adapter may know about `~/.hermes/config.yaml` and `~/.hermes/skills`.
A ChatGPT adapter may know about `conversations.json`, uploaded-file metadata,
and image attachment directories. A Claude adapter may know about Claude's
export shape and project boundaries. A generic adapter may only know about
memory JSON, conversation JSON, `SKILL.md`, and Markdown folders.

Nonstandard folders should be adapter details, not required Odysseus concepts.
