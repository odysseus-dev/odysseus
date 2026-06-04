// src/chat_processor.rs  <- src/chat_processor.py
//! Context-preface assembly for chat / agent LLM calls.
//!
//! PORT_PARTIAL (web). This is the faithful Rust port of `src/chat_processor.py`.
//! The whole module is `web`-gated: even though the BM25/IDF retrieval math is
//! pure CPU, the live `build_context_preface` reaches into the embedding-backed
//! memory-vector search, the HTTP RAG manager, and (in Python) the web-search /
//! url-fetch services — so it lands with the HTTP/vector cohort.
//!
//! ## What is a faithful 1:1 port
//! * [`content_tokens`] — the `[a-z0-9]+(?:[-_][a-z0-9]+)*` lower-case tokenizer
//!   filtered to len>=3 non-stopwords (`_content_tokens`).
//! * [`STOPWORDS`] — the exact frozenset, split on whitespace.
//! * [`ChatProcessor::hybrid_retrieve`] — the BM25/IDF scoring (`k1=1.5`,
//!   `b=0.75`, `idf = ln((N-df+0.5)/(df+0.5) + 1)`, binary tf), the `kw/6.0`
//!   normalization cap, the identity/contact/preference category boosts
//!   (1.4 / 1.3 / 1.2), the vector/no-vector gates and weighted blend, the
//!   `final > 0.12` floor, and the descending stable sort.
//! * [`ChatProcessor::build_context_preface`] — the preset prompt, the
//!   UNTRUSTED_CONTEXT_POLICY system message, pinned + retrieved memory blocks
//!   (`untrusted_context_message`), the usage-counter bump, the RAG
//!   threshold/`round(sim, 3)`/10000-char-truncation block, and the
//!   `_last_used_memories` bookkeeping.
//!
//! ## Search/skills wiring
//! * **Web search** (`comprehensive_web_search`) and **URL fetch**
//!   (`fetch_webpage_content`) are wired to the real `crate::src::search`
//!   engine. The web block runs when `use_web=True` (returning the formatted
//!   report + `web_sources`); the url-fetch block runs for each non-YouTube URL
//!   when the message isn't a long/link-heavy paste. Both calls are
//!   sync-blocking `reqwest`, exactly like the synchronous Python calls.
//!   DOCUMENTED DRIFT: the Rust `comprehensive_web_search` is infallible
//!   (it folds internal errors into the report text) where Python's can raise;
//!   the Python `except` branch that appends a "Web search encountered an error"
//!   system message is therefore unreachable in the port.
//! * **Skills index** (`skills_manager.index_for`) is modelled as an
//!   `Option<Arc<dyn SkillsIndex>>`. `app_initializer` now wires a real
//!   `crate::services::memory::skills::SkillsManager` adapter, so the agent-mode
//!   skills-index block fires exactly like Python when a manager is present.
//!
//! ## Collaborator shapes (duck-typed in Python -> narrow Rust traits)
//! Python's `ChatProcessor.__init__` takes four duck-typed collaborators and
//! only calls a tiny surface on each. To avoid a hard dependency on the
//! `rag_vector` / `memory_vector` modules (which land in a sibling step) we
//! model each collaborator by the *exact* surface this module touches:
//! * `memory_manager` -> the real [`crate::src::memory::MemoryManager`]
//!   (`.load(owner)` + `.increment_uses(ids)`), shared as `Arc`.
//! * `personal_docs_manager.rag_manager` -> [`RagSearch`] (`.search(query, k,
//!   owner)`), the owner-aware `VectorRAG.search` surface.
//! * `memory_vector` -> [`MemoryVectorSearch`] (`.healthy()` + `.search(query,
//!   k)` returning `{"memory_id", "score"}` dicts).
//! * `skills_manager` -> [`SkillsIndex`] (`.index_for(owner)`), deferred/None.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};

use crate::error::PyResult;
use crate::pylog as logger;
use crate::pytime;
use crate::src::chat_helpers::extract_urls;
use crate::src::memory::MemoryManager;
use crate::src::prompt_security::{untrusted_context_message, UNTRUSTED_CONTEXT_POLICY};
use crate::src::youtube_min::is_youtube_url;

// ---------------------------------------------------------------------------
// Stopwords & tokenizer
// ---------------------------------------------------------------------------

/// `_STOPWORDS` — the frozenset of stop words.
///
/// Python builds it from one big space-joined string literal then `.split()`
/// (split on runs of whitespace, dropping empties). We reproduce the exact
/// token list via `split_whitespace`, which has the same semantics.
static STOPWORDS: Lazy<std::collections::HashSet<&'static str>> = Lazy::new(|| {
    concat!(
        "a an the is am are was were be been being have has had do does did ",
        "will would shall should can could may might must need ought dare ",
        "i me my mine we us our ours you your yours he him his she her hers ",
        "it its they them their theirs this that these those ",
        "and but or nor not no so if then else than too also very ",
        "in on at to for of by with from up out about into over after ",
        "what when where which who whom how why all each every some any ",
        "just very really actually like well also still already even ",
        "oh ok okay yes yeah hey hi hello thanks thank please sorry ",
        "much more most own other another such only same here there ",
        "because while during before until since through between both ",
        "few many several some none nothing something anything everything ",
        "get got make made go going went been come came take took ",
        "know think want let say tell give see look find way thing ",
        "don doesn didn won wouldn couldn shouldn wasn weren isn aren haven hasn ",
        "don't doesn't didn't won't wouldn't couldn't shouldn't ",
        "it's i'm i've i'll i'd you're you've you'll he's she's we're we've they're they've ",
        "that's there's here's what's who's how's let's can't",
    )
    .split_whitespace()
    .collect()
});

/// `re.findall(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', text.lower())`.
static CONTENT_WORD_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[a-z0-9]+(?:[-_][a-z0-9]+)*").unwrap());

/// `_content_tokens(text) -> list`.
///
/// Extract meaningful content words: no stopwords, min 3 chars, lowercase.
/// Mirrors `re.findall(...)` over `text.lower()` then the comprehension filter
/// `len(w) >= 3 and w not in _STOPWORDS`. `len(w)` in Python counts code points;
/// the regex only matches ASCII `[a-z0-9]`-runs, so `chars().count()` and byte
/// length coincide, but we use `chars().count()` to stay literal.
pub fn content_tokens(text: &str) -> Vec<String> {
    let lowered = text.to_lowercase();
    CONTENT_WORD_RE
        .find_iter(&lowered)
        .map(|m| m.as_str().to_string())
        .filter(|w| w.chars().count() >= 3 && !STOPWORDS.contains(w.as_str()))
        .collect()
}

// ---------------------------------------------------------------------------
// Collaborator surfaces (duck-typed in Python)
// ---------------------------------------------------------------------------

/// The owner-aware RAG search surface (`personal_docs_manager.rag_manager`).
///
/// Python calls `rag_manager.search(message, k=5, owner=owner)` and reads
/// `r["metadata"]`, `r["document"]`, `r.get("similarity", 0)` off each result
/// dict. `rag_vector::VectorRAG` will implement this (the result shape is the
/// hybrid-search dict). Returns `Vec<Value>` to mirror `List[Dict[str, Any]]`.
pub trait RagSearch: Send + Sync {
    /// `rag_manager.search(query, k=5, owner=owner) -> List[Dict[str, Any]]`.
    fn search(&self, query: &str, k: usize, owner: Option<&str>) -> PyResult<Vec<Value>>;
}

/// The memory-vector search surface (`self.memory_vector`).
///
/// Python touches `self.memory_vector.healthy` (property) and
/// `self.memory_vector.search(message, k=...)` which returns dicts
/// `{"memory_id": str, "score": float}`. `memory_vector::MemoryVectorStore`
/// will implement this.
pub trait MemoryVectorSearch: Send + Sync {
    /// `self.memory_vector.healthy` — the readiness property.
    fn healthy(&self) -> bool;
    /// `self.memory_vector.search(query, k) -> List[{"memory_id","score"}]`.
    fn search(&self, query: &str, k: usize) -> Vec<Value>;
}

/// The skills-index surface (`self.skills_manager`).
///
/// Python calls `self.skills_manager.index_for(owner=owner)` and iterates the
/// returned skill dicts (`s.get("category")`, `s["name"]`, `s.get("description")`).
/// `app_initializer` wires a real `crate::services::memory::skills::SkillsManager`
/// adapter into this slot, so the agent-mode skills-index block fires whenever a
/// manager is present (mirroring Python's truthiness check on `self.skills_manager`).
pub trait SkillsIndex: Send + Sync {
    /// `self.skills_manager.index_for(owner=owner) -> List[Dict[str, Any]]`.
    fn index_for(&self, owner: Option<&str>) -> PyResult<Vec<Value>>;
}

// ---------------------------------------------------------------------------
// ChatProcessor
// ---------------------------------------------------------------------------

/// `ChatProcessor` — assembles the LLM context preface.
pub struct ChatProcessor {
    /// `self.memory_manager` — the JSON-file MemoryManager.
    memory_manager: Arc<MemoryManager>,
    /// `self.personal_docs_manager` — only its `.rag_manager` is touched.
    rag_manager: Option<Arc<dyn RagSearch>>,
    /// `self.memory_vector` — optional embedding-backed memory search.
    memory_vector: Option<Arc<dyn MemoryVectorSearch>>,
    /// `self.skills_manager` — optional; `app_initializer` wires a real
    /// `SkillsManager` adapter, mirroring Python's optional collaborator.
    skills_manager: Option<Arc<dyn SkillsIndex>>,
    /// `self._last_used_memories` — set per `build_context_preface` call, read
    /// back by callers (e.g. to attribute memory hits). Behind a `Mutex` because
    /// the Python attribute is mutated on `&self`.
    last_used_memories: Mutex<Vec<Value>>,
}

/// `RAG_SIMILARITY_THRESHOLD = 0.35` — minimum similarity for RAG injection.
pub const RAG_SIMILARITY_THRESHOLD: f64 = 0.35;

impl ChatProcessor {
    /// `__init__(self, memory_manager, personal_docs_manager, memory_vector=None,
    /// skills_manager=None)`.
    ///
    /// `personal_docs_manager` is modelled by the only surface this module uses
    /// (`getattr(personal_docs_manager, 'rag_manager', None)`): the optional
    /// owner-aware [`RagSearch`]. Pass `None` to reproduce the
    /// `rag_manager == None` (or missing-attribute) path.
    pub fn new(
        memory_manager: Arc<MemoryManager>,
        rag_manager: Option<Arc<dyn RagSearch>>,
        memory_vector: Option<Arc<dyn MemoryVectorSearch>>,
        skills_manager: Option<Arc<dyn SkillsIndex>>,
    ) -> Self {
        ChatProcessor {
            memory_manager,
            rag_manager,
            memory_vector,
            skills_manager,
            last_used_memories: Mutex::new(Vec::new()),
        }
    }

    /// Snapshot of `self._last_used_memories` (what the last preface injected).
    pub fn last_used_memories(&self) -> Vec<Value> {
        self.last_used_memories.lock().unwrap().clone()
    }

    /// `_hybrid_retrieve(self, message, mem_entries, k=5) -> list`.
    ///
    /// Retrieve memories relevant to the message. Uses BM25-style keyword scoring
    /// + optional vector similarity. Recency is a tiebreaker only, never the
    /// primary signal.
    ///
    /// `mem_entries` are the `serde_json::Value` memory dicts (`m["text"]`,
    /// `m["id"]`, `m.get("category", "fact")`, `m.get("timestamp", 0)`). Returns
    /// the chosen entries (cloned), top-`k`, in descending score order.
    pub fn hybrid_retrieve(&self, message: &str, mem_entries: &[Value], k: usize) -> Vec<Value> {
        // `if not mem_entries or not message.strip(): return []`
        if mem_entries.is_empty() || message.trim().is_empty() {
            return Vec::new();
        }

        let now = pytime::time();
        let query_tokens = content_tokens(message);

        // `has_vector = self.memory_vector and self.memory_vector.healthy`
        let has_vector = self
            .memory_vector
            .as_ref()
            .map(|mv| mv.healthy())
            .unwrap_or(false);

        // If the query has no meaningful tokens, skip keyword retrieval entirely.
        // Python: `if not query_tokens:` -> fall back to vector-only if available,
        // else return []. (When vector IS available, execution continues; the
        // keyword scores will all be 0 and the vector gate/blend decides.)
        if query_tokens.is_empty() && !has_vector {
            return Vec::new();
        }

        // ── Build IDF from the memory corpus ──
        // `N = len(mem_entries)`
        let n = mem_entries.len();
        let n_f = n as f64;

        // `doc_freq` (token -> #memories containing it) and
        // `mem_token_cache` (mem_id -> set of content tokens).
        let mut doc_freq: HashMap<String, usize> = HashMap::new();
        // Preserve per-entry token sets aligned to `mem_entries` order; keyed by
        // id for the cache-by-id semantics, but we also keep the index so a
        // duplicate id can't lose an entry's contribution to the corpus sums.
        let mut mem_tokens: Vec<std::collections::HashSet<String>> = Vec::with_capacity(n);
        let mut mem_token_cache: HashMap<String, std::collections::HashSet<String>> =
            HashMap::new();
        for mem in mem_entries.iter() {
            let text = mem.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let toks: std::collections::HashSet<String> =
                content_tokens(text).into_iter().collect();
            let mid = mem_id(mem);
            // `mem_token_cache[mem["id"]] = toks` (later ids overwrite earlier).
            mem_token_cache.insert(mid, toks.clone());
            // `for t in toks: doc_freq[t] += 1` — counts every entry's tokens
            // (the per-token sets are deduped, so binary presence per memory).
            for t in &toks {
                *doc_freq.entry(t.clone()).or_insert(0) += 1;
            }
            mem_tokens.push(toks);
        }

        // `avg_len = max(sum(len(v) for v in mem_token_cache.values()) / N, 1)`.
        // Constant across `_bm25_score` calls, so computed once. NOTE: it sums
        // over the *cache* values (deduped by id), matching Python exactly.
        let cache_total_len: usize = mem_token_cache.values().map(|s| s.len()).sum();
        let avg_len = ((cache_total_len as f64) / n_f).max(1.0);
        let k1 = 1.5_f64;
        let b = 0.75_f64;

        // `_bm25_score(query_toks, mem_id)` — closure over doc_freq/avg_len.
        let bm25_score = |query_toks: &[String], mem_toks: &std::collections::HashSet<String>| -> f64 {
            if mem_toks.is_empty() || query_toks.is_empty() {
                return 0.0;
            }
            let mut score = 0.0_f64;
            let mem_len = mem_toks.len() as f64;
            for qt in query_toks {
                // `if qt not in mem_toks: continue`
                if !mem_toks.contains(qt) {
                    continue;
                }
                let df = *doc_freq.get(qt).unwrap_or(&0) as f64;
                // `idf = math.log((N - df + 0.5) / (df + 0.5) + 1)`
                let idf = ((n_f - df + 0.5) / (df + 0.5) + 1.0).ln();
                // `tf = 1` binary; `tf_norm = (tf*(k1+1)) / (tf + k1*(1-b+b*mem_len/avg_len))`
                let tf = 1.0_f64;
                let tf_norm = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * mem_len / avg_len));
                score += idf * tf_norm;
            }
            score
        };

        // ── Vector scores ──
        // `vector_scores = {}`
        let mut vector_scores: HashMap<String, f64> = HashMap::new();
        if has_vector {
            if let Some(mv) = &self.memory_vector {
                // `self.memory_vector.search(message, k=min(k*3, 20))`
                let search_k = std::cmp::min(k.saturating_mul(3), 20);
                let results = mv.search(message, search_k);
                // `mem_by_id = {m["id"]: m for m in mem_entries}` — membership only.
                let mut mem_ids: std::collections::HashSet<String> =
                    std::collections::HashSet::new();
                for m in mem_entries.iter() {
                    mem_ids.insert(mem_id(m));
                }
                for r in &results {
                    let rid = r
                        .get("memory_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    if mem_ids.contains(&rid) {
                        // `vector_scores[r["memory_id"]] = max(r["score"], 0.0)`
                        let score = r.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        vector_scores.insert(rid, score.max(0.0));
                    }
                }
            }
        }

        // ── Score all candidates ──
        // `scored = []` of (final, mem). Preserve insertion order for the
        // stable descending sort.
        let mut scored: Vec<(f64, &Value)> = Vec::new();
        for (i, mem) in mem_entries.iter().enumerate() {
            let mid = mem_id(mem);
            let vs = *vector_scores.get(&mid).unwrap_or(&0.0);
            let kw = bm25_score(&query_tokens, &mem_tokens[i]);

            // `kw_norm = min(kw / 6.0, 1.0) if kw > 0 else 0.0`
            let mut kw_norm = if kw > 0.0 { (kw / 6.0).min(1.0) } else { 0.0 };

            // Category-aware boost for identity/contact queries.
            let category = mem
                .get("category")
                .and_then(|v| v.as_str())
                .unwrap_or("fact");
            let msg_lower = message.to_lowercase();
            let mem_text = mem.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let mem_lower = mem_text.to_lowercase();
            let mut cat_boost = 1.0_f64;
            if ["name", "who am i", "my name"]
                .iter()
                .any(|w| msg_lower.contains(w))
            {
                if category == "identity"
                    || ["name is", "i am", "called"]
                        .iter()
                        .any(|w| mem_lower.contains(w))
                {
                    cat_boost = 1.4;
                }
            } else if ["phone", "email", "address", "contact"]
                .iter()
                .any(|w| msg_lower.contains(w))
            {
                if category == "contact" || mem_lower.contains('@') {
                    cat_boost = 1.3;
                }
            } else if ["like", "prefer", "favorite"]
                .iter()
                .any(|w| msg_lower.contains(w))
                && category == "preference"
            {
                cat_boost = 1.2;
            }

            // `kw_norm = min(kw_norm * cat_boost, 1.0)`
            kw_norm = (kw_norm * cat_boost).min(1.0);

            // Recency — tiebreaker only (max 5% contribution).
            // `ts = mem.get("timestamp", 0)`; `days_old = max((now-ts)/86400, 0)`
            let ts = mem.get("timestamp").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let days_old = ((now - ts) / 86400.0).max(0.0);
            let recency = 1.0 / (1.0 + days_old * 0.05);

            // Gate + blend.
            let final_score = if has_vector {
                // `if vs < 0.20 and kw_norm < 0.08: continue`
                if vs < 0.20 && kw_norm < 0.08 {
                    continue;
                }
                (0.55 * vs) + (0.40 * kw_norm) + (0.05 * recency)
            } else {
                // `if kw_norm < 0.08: continue`
                if kw_norm < 0.08 {
                    continue;
                }
                (0.95 * kw_norm) + (0.05 * recency)
            };

            // `if final > 0.12: scored.append((final, mem))`
            if final_score > 0.12 {
                scored.push((final_score, mem));
            }
        }

        // `scored.sort(key=lambda x: x[0], reverse=True)` — Python's sort is
        // stable; `sort_by` is stable, so equal scores keep insertion order.
        // `reverse=True` -> descending by score.
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

        // `return [mem for _, mem in scored[:k]]`
        scored
            .into_iter()
            .take(k)
            .map(|(_, mem)| mem.clone())
            .collect()
    }

    /// `build_context_preface(...) -> Tuple[preface, rag_sources, web_sources]`.
    ///
    /// Build the context preface for LLM calls. Returns the preface messages
    /// (`Vec<Value>`), the RAG sources list, and the web sources list. The
    /// `session` parameter is accepted (mirroring the Python signature) but, like
    /// Python, this function never reads it.
    ///
    /// `use_web` runs the real `comprehensive_web_search`; the URL-fetch loop
    /// runs the real `fetch_webpage_content`. `use_skills`/`agent_mode`
    /// skills-index injection fires when a `skills_manager` is present.
    #[allow(clippy::too_many_arguments)]
    pub fn build_context_preface(
        &self,
        message: &str,
        use_web: bool,
        use_rag: bool,
        use_memory: bool,
        time_filter: Option<&str>,
        preset_system_prompt: Option<&str>,
        owner: Option<&str>,
        _character_name: Option<&str>,
        agent_mode: bool,
        incognito: bool,
        use_skills: bool,
    ) -> (Vec<Value>, Vec<Value>, Vec<Value>) {
        let mut preface: Vec<Value> = Vec::new();
        let mut rag_sources: Vec<Value> = Vec::new();

        // Add preset system prompt if specified.
        if let Some(ps) = preset_system_prompt {
            preface.push(json!({"role": "system", "content": ps}));
        }
        preface.push(json!({
            "role": "system",
            "content": UNTRUSTED_CONTEXT_POLICY,
        }));

        // Memory: pinned (always included) + extended (RAG-retrieved when relevant).
        // `self._last_used_memories = []`
        let mut last_used: Vec<Value> = Vec::new();
        if use_memory {
            let mem_entries = self.memory_manager.load(owner);

            // `pinned = [m for m in mem_entries if m.get("pinned")]`
            // `extended = [m for m in mem_entries if not m.get("pinned")]`
            // Python truthiness: `m.get("pinned")` is truthy for `True` and any
            // non-empty/non-zero value; missing/`None`/`False`/`0`/`""` are falsy.
            let pinned: Vec<&Value> =
                mem_entries.iter().filter(|m| is_truthy(m, "pinned")).collect();
            let extended: Vec<Value> = mem_entries
                .iter()
                .filter(|m| !is_truthy(m, "pinned"))
                .cloned()
                .collect();

            let mut used_ids: Vec<String> = Vec::new();
            if !pinned.is_empty() {
                // `pinned_text = "\n- ".join([m["text"] for m in pinned])`
                let pinned_texts: Vec<&str> = pinned
                    .iter()
                    .map(|m| m.get("text").and_then(|v| v.as_str()).unwrap_or(""))
                    .collect();
                let pinned_text = pinned_texts.join("\n- ");
                preface.push(untrusted_context_message(
                    "saved memory: pinned user facts",
                    Some(&format!("Core facts about the user:\n- {pinned_text}")),
                ));
                for m in &pinned {
                    let cat = m
                        .get("category")
                        .and_then(|v| v.as_str())
                        .unwrap_or("fact");
                    let text = m.get("text").and_then(|v| v.as_str()).unwrap_or("");
                    last_used.push(json!({"text": text, "category": cat, "type": "pinned"}));
                    // `if m.get("id"): _used_ids.append(m["id"])` — truthy id only.
                    if let Some(id) = m.get("id").and_then(|v| v.as_str()) {
                        if !id.is_empty() {
                            used_ids.push(id.to_string());
                        }
                    }
                }
            }

            if !extended.is_empty() {
                // `relevant = self._hybrid_retrieve(message, extended, k=3)`
                let relevant = self.hybrid_retrieve(message, &extended, 3);
                if !relevant.is_empty() {
                    // `ext_text = "\n".join([f"- {m['text']}" for m in relevant])`
                    let ext_lines: Vec<String> = relevant
                        .iter()
                        .map(|m| {
                            let t = m.get("text").and_then(|v| v.as_str()).unwrap_or("");
                            format!("- {t}")
                        })
                        .collect();
                    let ext_text = ext_lines.join("\n");
                    preface.push(untrusted_context_message(
                        "saved memory: retrieved context",
                        Some(&format!(
                            "Memory context. Do not reference unless the user asks \
                             about these topics.\n{ext_text}"
                        )),
                    ));
                    for m in &relevant {
                        let cat = m
                            .get("category")
                            .and_then(|v| v.as_str())
                            .unwrap_or("fact");
                        let text = m.get("text").and_then(|v| v.as_str()).unwrap_or("");
                        last_used
                            .push(json!({"text": text, "category": cat, "type": "recalled"}));
                        if let Some(id) = m.get("id").and_then(|v| v.as_str()) {
                            if !id.is_empty() {
                                used_ids.push(id.to_string());
                            }
                        }
                    }
                }
            }

            // Bump usage counters for the memories that were actually injected.
            // Python: `if _used_ids and hasattr(self.memory_manager, "increment_uses")`.
            // `MemoryManager` always has `increment_uses`, so the hasattr is True.
            if !used_ids.is_empty() {
                if let Err(e) = self.memory_manager.increment_uses(&used_ids) {
                    logger::warning(&format!("Failed to increment memory uses: {e}"));
                }
            }
        }

        // Publish the injected-memory bookkeeping.
        *self.last_used_memories.lock().unwrap() = last_used;

        // RAG: search if enabled and rag_manager available, inject only above
        // threshold. The whole block is wrapped in Python's `try/except` -> we
        // map a `Err` from the trait to the same `logger.warning(...)` no-op.
        if use_rag {
            let rag_result = (|| -> PyResult<()> {
                if let Some(rag) = &self.rag_manager {
                    // `results = rag_manager.search(message, k=5, owner=owner)`
                    let results = rag.search(message, 5, owner)?;
                    // `relevant = [r for r in results if r.get("similarity", 0) >= THRESHOLD]`
                    let relevant: Vec<&Value> = results
                        .iter()
                        .filter(|r| {
                            r.get("similarity").and_then(|v| v.as_f64()).unwrap_or(0.0)
                                >= RAG_SIMILARITY_THRESHOLD
                        })
                        .collect();
                    if !relevant.is_empty() {
                        logger::info(&format!(
                            "RAG: {}/{} results above threshold {}",
                            relevant.len(),
                            results.len(),
                            RAG_SIMILARITY_THRESHOLD
                        ));
                        // Build rag_sources: filename (meta.filename or
                        // meta.source or "unknown"), snippet (document[:200]),
                        // similarity (round(sim, 3)).
                        rag_sources = relevant
                            .iter()
                            .map(|r| {
                                let meta = r.get("metadata");
                                let filename = meta
                                    .and_then(|m| m.get("filename"))
                                    .and_then(|v| v.as_str())
                                    .or_else(|| {
                                        meta.and_then(|m| m.get("source"))
                                            .and_then(|v| v.as_str())
                                    })
                                    .unwrap_or("unknown")
                                    .to_string();
                                let doc =
                                    r.get("document").and_then(|v| v.as_str()).unwrap_or("");
                                let snippet = char_slice(doc, 200);
                                let sim = r
                                    .get("similarity")
                                    .and_then(|v| v.as_f64())
                                    .unwrap_or(0.0);
                                json!({
                                    "filename": filename,
                                    "snippet": snippet,
                                    "similarity": round3(sim),
                                })
                            })
                            .collect();
                        // `rag_content = "Relevant documents:\n\n" + "\n\n---\n\n".join(
                        //      f"[{s['filename']}]\n{r['document']}"
                        //      for s, r in zip(rag_sources, relevant))`
                        let blocks: Vec<String> = rag_sources
                            .iter()
                            .zip(relevant.iter())
                            .map(|(s, r)| {
                                let fname =
                                    s.get("filename").and_then(|v| v.as_str()).unwrap_or("");
                                let doc =
                                    r.get("document").and_then(|v| v.as_str()).unwrap_or("");
                                format!("[{fname}]\n{doc}")
                            })
                            .collect();
                        let mut rag_content =
                            format!("Relevant documents:\n\n{}", blocks.join("\n\n---\n\n"));
                        // `if len(rag_content) > 10000: rag_content = rag_content[:10000] + "\n[Truncated]"`
                        // Python `len`/slice are by code point.
                        if rag_content.chars().count() > 10000 {
                            rag_content = format!("{}\n[Truncated]", char_slice(&rag_content, 10000));
                        }
                        preface.push(untrusted_context_message(
                            "retrieved documents",
                            Some(&rag_content),
                        ));
                    }
                }
                Ok(())
            })();
            if let Err(e) = rag_result {
                logger::warning(&format!("RAG retrieval failed: {e}"));
            }
        }

        // Add web search if enabled — wired to the real `crate::src::search`
        // engine. Python:
        //   web_context, web_sources = comprehensive_web_search(
        //       message, time_filter=time_filter, return_sources=True)
        // returns `(report, sources)`; on exception it appends the "Web search
        // encountered an error" system message. `comprehensive_web_search` is
        // sync-blocking (`reqwest`), exactly like the synchronous Python call.
        let mut web_sources: Vec<Value> = Vec::new();
        if use_web {
            use crate::src::search::ComprehensiveResult;
            // `comprehensive_web_search` is sync and uses `reqwest::blocking`, whose
            // client creates+drops its OWN runtime. `build_context_preface` is sync
            // but is reached from the async `build_chat_context` running on a tokio
            // worker, where creating/dropping that runtime PANICS ("Cannot drop a
            // runtime in a context where blocking is not allowed"). Run it on a
            // dedicated OS thread (no ambient runtime) — the off-worker equivalent
            // of the `spawn_blocking` the async callers (search_routes /
            // research_handler / tool_execution) already use. max_pages/max_workers
            // take Python defaults (3 / 4); other filters None.
            let msg_owned = message.to_string();
            let tf_owned = time_filter.map(|s| s.to_string());
            let result = std::thread::spawn(move || {
                crate::src::search::comprehensive_web_search(
                    &msg_owned, 3, 4, tf_owned.as_deref(), None, None, None, None, 0, true,
                )
            })
            .join();
            match result {
                Ok(ComprehensiveResult::WithSources(web_context, sources)) => {
                    web_sources = sources;
                    preface.push(untrusted_context_message(
                        "web search results",
                        Some(&web_context),
                    ));
                }
                // `return_sources=true` always yields WithSources; Report is
                // unreachable but handled so we never silently drop the block.
                Ok(ComprehensiveResult::Report(web_context)) => {
                    preface.push(untrusted_context_message(
                        "web search results",
                        Some(&web_context),
                    ));
                }
                // Search thread panicked — best-effort, skip the block (Python
                // `except` appends a "search error" note; we omit the context).
                Err(_) => {
                    logger::warning(
                        "build_context_preface: web search thread panicked; skipping web context",
                    );
                }
            }
        }

        // Process non-YouTube URLs in message (YouTube handled by
        // preprocess_message). The skip-fetch heuristic is ported; the
        // `fetch_webpage_content` call is wired to the real `crate::src::search`
        // engine (sync-blocking `reqwest`, like the synchronous Python call).
        let urls = extract_urls(message);
        let non_yt_urls: Vec<String> =
            urls.into_iter().filter(|u| !is_youtube_url(u)).collect();
        // `skip_url_fetch = len(message) > 2000 or len(non_yt_urls) > 3`
        let skip_url_fetch = message.chars().count() > 2000 || non_yt_urls.len() > 3;
        if !skip_url_fetch {
            for url in &non_yt_urls {
                // result = fetch_webpage_content(url)  (Python defaults timeout=5, retry=0)
                // Same `reqwest::blocking`-on-the-async-worker hazard as the web
                // search above — run it on a dedicated OS thread. A panic skips the
                // URL (best-effort, like an unreachable page).
                let url_owned = url.clone();
                let result = match std::thread::spawn(move || {
                    crate::src::search::fetch_webpage_content(&url_owned, 5, 0)
                })
                .join()
                {
                    Ok(r) => r,
                    Err(_) => continue,
                };
                if result.get("success").and_then(Value::as_bool).unwrap_or(false) {
                    // content = result.get('content', '')[:10000]  (code points)
                    let content_full =
                        result.get("content").and_then(Value::as_str).unwrap_or("");
                    let content = char_slice(content_full, 10000);
                    preface.push(untrusted_context_message(
                        &format!("web page: {url}"),
                        Some(&format!("Content from {url}:\n\n{content}")),
                    ));
                }
            }
        }

        // Skills index — progressive disclosure. Only when agent_mode and not
        // incognito and use_skills and a skills_manager is present. The real
        // `crate::services::memory::skills::SkillsManager` adapter is wired in by
        // `app_initializer`, so this fires exactly when Python's
        // `self.skills_manager` is truthy.
        if agent_mode && !incognito && use_skills && self.skills_manager.is_some() {
            if let Some(sm) = &self.skills_manager {
                // `idx = self.skills_manager.index_for(owner=owner)` (try/except -> []).
                let idx = sm.index_for(owner).unwrap_or_default();
                if !idx.is_empty() {
                    // Group by category (or "general"), then render sorted.
                    let mut by_cat: std::collections::BTreeMap<String, Vec<&Value>> =
                        std::collections::BTreeMap::new();
                    for s in &idx {
                        let cat = s
                            .get("category")
                            .and_then(|v| v.as_str())
                            .filter(|c| !c.is_empty())
                            .unwrap_or("general")
                            .to_string();
                        by_cat.entry(cat).or_default().push(s);
                    }
                    let mut lines: Vec<String> = vec![
                        "[Available skills — call manage_skills(action='view', name='...') \
                         to load one when relevant]"
                            .to_string(),
                    ];
                    // `for cat in sorted(by_cat)` — BTreeMap iterates sorted.
                    for (cat, skills) in &by_cat {
                        lines.push(format!("  {cat}:"));
                        // `for s in sorted(by_cat[cat], key=lambda x: x["name"])`
                        let mut sorted_skills = skills.clone();
                        sorted_skills.sort_by(|a, b| {
                            let an = a.get("name").and_then(|v| v.as_str()).unwrap_or("");
                            let bn = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
                            an.cmp(bn)
                        });
                        for s in sorted_skills {
                            let name = s.get("name").and_then(|v| v.as_str()).unwrap_or("");
                            let desc =
                                s.get("description").and_then(|v| v.as_str()).unwrap_or("");
                            if desc.is_empty() {
                                lines.push(format!("    - {name}"));
                            } else {
                                lines.push(format!("    - {name}: {desc}"));
                            }
                        }
                    }
                    preface.push(untrusted_context_message(
                        "available skills index",
                        Some(&lines.join("\n")),
                    ));
                }
            }
        }

        (preface, rag_sources, web_sources)
    }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/// `mem["id"]` as a String. Python memory dicts always carry a string `id`
/// (assigned at validation time); a missing/non-string id maps to `""`, which
/// is what `dict.get` would feed into the `mem_by_id`/`vector_scores` keying.
fn mem_id(mem: &Value) -> String {
    mem.get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// Python truthiness of `m.get(key)`: `None`/missing/`false`/`0`/`""`/`[]`/`{}`
/// are falsy; everything else is truthy. Used for `m.get("pinned")`.
fn is_truthy(m: &Value, key: &str) -> bool {
    match m.get(key) {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `s[:n]` by code points (Python str slicing).
fn char_slice(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `round(x, 3)` with Python 3 banker's rounding (round-half-to-even).
fn round3(x: f64) -> f64 {
    let scaled = x * 1000.0;
    round_half_even(scaled) / 1000.0
}

/// Round-half-to-even (Python 3 `round`).
fn round_half_even(x: f64) -> f64 {
    let floor = x.floor();
    let diff = x - floor;
    if diff < 0.5 {
        floor
    } else if diff > 0.5 {
        floor + 1.0
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Tokenizer parity ──

    #[test]
    fn content_tokens_filters_stopwords_and_short() {
        // "the" stopword dropped; "is" stopword; "ai" len 2 dropped; keeps
        // "rust", "fast", "code".
        let toks = content_tokens("The Rust is AI fast code");
        assert_eq!(toks, vec!["rust", "fast", "code"]);
    }

    #[test]
    fn content_tokens_keeps_hyphen_runs() {
        // `[a-z0-9]+(?:[-_][a-z0-9]+)*` keeps internal hyphen/underscore runs.
        let toks = content_tokens("multi-word_token simple");
        assert_eq!(toks, vec!["multi-word_token", "simple"]);
    }

    #[test]
    fn content_tokens_empty_when_all_stopwords() {
        assert!(content_tokens("the a is are you me").is_empty());
    }

    // ── Helper parity ──

    #[test]
    fn truthy_pinned() {
        assert!(is_truthy(&json!({"pinned": true}), "pinned"));
        assert!(!is_truthy(&json!({"pinned": false}), "pinned"));
        assert!(!is_truthy(&json!({}), "pinned"));
        assert!(!is_truthy(&json!({"pinned": null}), "pinned"));
        assert!(!is_truthy(&json!({"pinned": 0}), "pinned"));
        assert!(is_truthy(&json!({"pinned": 1}), "pinned"));
        assert!(!is_truthy(&json!({"pinned": ""}), "pinned"));
    }

    #[test]
    fn round3_banker() {
        // round-half-to-even
        assert_eq!(round3(0.1235), 0.124); // .5 -> even (4)
        assert_eq!(round3(0.1245), 0.124); // .5 -> even (4)
        assert_eq!(round3(0.5), 0.5);
    }

    #[test]
    fn char_slice_codepoints() {
        assert_eq!(char_slice("hello", 3), "hel");
        assert_eq!(char_slice("café", 4), "café");
        assert_eq!(char_slice("ab", 10), "ab");
    }

    // ── Hybrid retrieve (no-vector keyword path) ──

    fn mk_mem(id: &str, text: &str, ts: f64) -> Value {
        json!({"id": id, "text": text, "category": "fact", "timestamp": ts})
    }

    fn processor_no_collaborators() -> ChatProcessor {
        // A MemoryManager whose backing dir doesn't matter for hybrid_retrieve
        // (it takes mem_entries directly), but `MemoryManager::new` ensures
        // `<dir>/memory.json` exists, so the dir must be present first. Use a
        // per-call unique temp dir to avoid cross-test interference.
        static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let n = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let tmp = std::env::temp_dir().join(format!(
            "odysseus_cp_test_{}_{}",
            std::process::id(),
            n
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let mgr = MemoryManager::new(tmp.to_str().unwrap()).unwrap();
        ChatProcessor::new(Arc::new(mgr), None, None, None)
    }

    #[test]
    fn hybrid_retrieve_empty_inputs() {
        let cp = processor_no_collaborators();
        assert!(cp.hybrid_retrieve("anything", &[], 5).is_empty());
        let entries = vec![mk_mem("1", "rust programming language", 0.0)];
        // empty/whitespace message -> []
        assert!(cp.hybrid_retrieve("   ", &entries, 5).is_empty());
    }

    #[test]
    fn hybrid_retrieve_no_query_tokens_no_vector() {
        let cp = processor_no_collaborators();
        let entries = vec![mk_mem("1", "rust programming language", 0.0)];
        // Message has only stopwords/short tokens -> no query tokens, no vector
        // -> [].
        assert!(cp.hybrid_retrieve("the a is", &entries, 5).is_empty());
    }

    #[test]
    fn hybrid_retrieve_keyword_match_ranks() {
        let cp = processor_no_collaborators();
        let now = pytime::time();
        let entries = vec![
            mk_mem("1", "favorite programming language is rust", now),
            mk_mem("2", "the weather today is sunny outside", now),
            mk_mem("3", "rust borrow checker ownership memory", now),
        ];
        let out = cp.hybrid_retrieve("rust programming", &entries, 5);
        // Verified against the real Python `_hybrid_retrieve('rust programming',
        // ...)`: only mem 1 (which matches BOTH query tokens) clears the >0.12
        // gate. Mem 3 matches only "rust" (single-token, common-df) and falls
        // below the floor; mem 2 has no overlap. Output == ["1"].
        let ids: Vec<&str> = out
            .iter()
            .map(|m| m.get("id").and_then(|v| v.as_str()).unwrap())
            .collect();
        assert_eq!(ids, vec!["1"]);
    }

    #[test]
    fn hybrid_retrieve_respects_k() {
        let cp = processor_no_collaborators();
        let now = pytime::time();
        // Verified against real Python: query "rust ownership borrow" matches
        // multiple tokens per memory so 1 & 2 clear the gate; k caps the output.
        let entries = vec![
            mk_mem("1", "rust ownership borrow checker lifetimes", now),
            mk_mem("2", "rust ownership borrow memory safety", now),
            mk_mem("3", "rust ownership cargo crates ecosystem", now),
        ];
        let out = cp.hybrid_retrieve("rust ownership borrow", &entries, 2);
        let ids: Vec<&str> = out
            .iter()
            .map(|m| m.get("id").and_then(|v| v.as_str()).unwrap())
            .collect();
        assert_eq!(ids, vec!["1", "2"]);
    }
}
