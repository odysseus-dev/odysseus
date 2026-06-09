## Feature Request: Native TRACE-inspired Hierarchical Memory Engine

### 1. Problem Or Motivation

Odysseus currently relies on a flat fact-store (`memory.json`) and vector search for long-term memory. While functional, this architecture has several limitations:

- **No episodic structure**: Conversations are not organized into topics or sessions, making it hard to recall "what we discussed about X last week" with surrounding context.
- **No structured profile memory**: User preferences, allergies, names, etc. are stored as unstructured text facts, making CRUD operations unreliable and requiring the LLM to parse free-form memory entries.
- **No memory summarization / compaction**: As memory grows, retrieval quality degrades because old entries are never summarized or pruned.
- **External dependencies**: Previous attempts to integrate MemMachine or TRACE required external servers or forks, adding operational complexity.

We need a **native, self-contained** memory system that provides:
1. Hierarchical episodic memory (topic trees)
2. Structured profile memory (key-value with upsert)
3. Semantic multi-path retrieval
4. Background summarization/compaction
5. Optional LLM-based topic classification (opt-in, heuristic by default)

### 2. Proposed Solution

Implement a modular memory engine under `src/memory_engine/` with the following components:

- **`episodic_tree.py`**: JSON-backed hierarchical topic tree. Each node is a `TopicNode` containing `MessageNode` children. New messages are classified into existing topics or branched into new ones using Jaccard similarity + keyword overlap (heuristic by default).
- **`profile_manager.py`**: Key-value profile store with upsert-by-key semantics. Each entry tracks confidence and source. Persisted per-owner as `profile.json`.
- **`topic_classifier.py`**: Fast local heuristic classifier with an optional LLM-based mode gated by a `memory_llm_topic_classification` setting.
- **`prompt_synthesizer.py`**: Multi-path retrieval pipeline: embed query → ChromaDB topic search → walk topic ancestry → deduplicate → rank → format as compact XML context.
- **`tree_reorganizer.py`**: Background task that merges similar topics, prunes stale leaf nodes, and summarizes long message threads.
- **`enhanced_provider.py`**: `MemoryProvider` implementation that delegates to profile manager, legacy fact store, and episodic tree with tiered recall priority.

**Integration changes:**
- Wire `EnhancedMemoryProvider` into `app_initializer.py` as the default provider.
- Add episodic ingestion hook in `run_post_response_tasks` (records every user/assistant exchange into the topic tree).
- Add `user_profile_update`, `user_profile_get`, `user_profile_delete` agent tools.
- Add `memory_llm_topic_classification` toggle in System settings tab.

**Performance considerations:**
- Heuristic topic classification is O(n) on topic count and adds <1ms per message.
- Episodic ingestion is fire-and-forget via `asyncio.create_task` — never blocks the response stream.
- LLM topic classification is gated behind a settings toggle and disabled by default.
- Background reorganization is triggered on an interval (configurable via `memory_reorg_interval_messages`) and runs only when the tree exceeds a message threshold.

### 3. Alternatives Considered

| Alternative | Why Not Chosen |
|---|---|
| **Integrate MemMachine server** | Adds external dependency; deployment complexity. We already explored this in PR #2669 (MemMachine integration). |
| **Integrate `husain34/odysseus-trace` fork directly** | The fork was shallow and diverged significantly from upstream. Building natively gives us clean integration and avoids merge conflicts. |
| **Use only vector search** | No hierarchical structure, no episodic context, no summarization. Does not solve the core problem. |
| **Store everything in SQLite** | Adds schema migration burden; JSON files align with Odysseus's existing persistence model (memory.json, sessions.json). |

### 4. Related Issues

- PR #2669 — MemMachine integration (coexistence mode, external server dependency)
- This feature builds on lessons learned from #2669 but replaces the external dependency with a fully native implementation.
- Targets upstream `dev` branch, rebased cleanly with no merge commits.
