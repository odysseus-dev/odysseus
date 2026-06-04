// src/deep_research.rs  <- src/deep_research.py
//! IterResearch-style deep research engine.
//!
//! Implements an iterative Think -> Search -> Extract -> Synthesize loop where
//! the LLM drives every decision: what to search, what's relevant, what's
//! missing, and when to stop. Inspired by Alibaba's IterResearch approach.
//!
//! ## Port classification — PORT (web)
//!
//! The full async engine is ported faithfully: the `research()` round loop, the
//! two concurrency fan-outs (search-all-queries then fetch-and-extract-all-urls,
//! via `futures_util::future::join_all` with `return_exceptions` semantics
//! modelled as a per-task `Result`), the `_llm` helper (`llm_call_async` +
//! `strip_thinking`), and every parse / format helper including the three-tier
//! `_parse_json_array` (raw -> greedy-regex -> truncated-array repair).
//!
//! ### Search/fetch back-ends (now wired to the real search engine)
//!
//! * `_search` mirrors Python: resolves the provider from
//!   `search_provider_override` -> `settings["research_search_provider"]` ->
//!   `settings["search_provider"]`/`"searxng"`, short-circuits on `"disabled"`,
//!   walks `crate::src::search::_build_provider_chain` calling
//!   `crate::src::search::_call_provider(prov, query, 10)` inside
//!   `tokio::task::spawn_blocking` (the providers are sync-blocking `reqwest`,
//!   the `asyncio.to_thread` analogue), records the provider on the first
//!   non-empty hit, and records `last_search_error` per failing provider.
//! * `_fetch_and_extract` mirrors Python: emits the `reading` phase, fetches via
//!   `crate::src::search::fetch_webpage_content(url, 10)` inside
//!   `spawn_blocking`, bails on `!success`/empty content, truncates to
//!   `max_content_chars` at a paragraph boundary, then runs the LLM extraction.
//!
//! ### Documented deviations
//!
//! * Prompts are `const &str` with manual placeholder substitution rather than
//!   Python `str.format`, because the templates embed literal `{{ }}` example
//!   JSON that `format!` would choke on. The placeholder set is identical.
//! * `llm_call_async` (Rust) takes no `max_retries` kwarg; the Python `_llm`
//!   never passed one anyway, so there is no behavioural drift here.
//! * Cancellation is cooperative via an `Arc<AtomicBool>` (Python sets a plain
//!   `self._cancelled` bool); `cancel()` flips it so an in-flight `research()`
//!   sees it at the next round boundary.

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use once_cell::sync::Lazy;
use serde_json::{Map, Value};

use crate::pytime;
use crate::src::goal_based_extractor::EXTRACTOR_PROMPT;
use crate::src::research_utils::{is_low_quality, strip_thinking};

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

/// `RESEARCH_PLAN_PROMPT` — note the literal `{{ }}` example JSON is preserved
/// verbatim; substitution only touches the `{question}` placeholder.
const RESEARCH_PLAN_PROMPT: &str = r#"You are a research strategist. Before searching, analyze this question and create a research plan.

**Question:** {question}

Break this question down:
1. What are the key sub-topics that need to be covered for a comprehensive answer?
2. What specific data points, facts, or perspectives should we look for?
3. What would a complete, high-quality answer include?

Return a JSON object with:
- "sub_questions": Array of 3-6 specific sub-questions to investigate
- "key_topics": Array of key topics/angles to cover
- "success_criteria": One sentence describing what a complete answer looks like

Example:
{{
  "sub_questions": ["What is the cost of living in X?", "How is the healthcare system?"],
  "key_topics": ["economy", "healthcare", "safety", "culture"],
  "success_criteria": "A balanced comparison covering cost, quality of life, and practical considerations."
}}
"#;

/// `QUERY_GEN_PROMPT`.
const QUERY_GEN_PROMPT: &str = r#"You are a research assistant planning web searches.

**Original question:** {question}

**Research plan:**
{research_plan}

**What we know so far:**
{report}

**Round:** {round_num}

Generate {num_queries} focused search queries that will help answer the question.
{round_instruction}

Return ONLY a JSON array of query strings, nothing else.
Example: ["query one", "query two", "query three"]
"#;

/// `SYNTHESIZE_PROMPT`.
const SYNTHESIZE_PROMPT: &str = r#"You are updating an evolving research report.

**Original question:** {question}

**Current report:**
{report}

**New findings from this round:**
{new_findings}

Integrate the new findings into the existing report. Produce an updated, well-organized report that answers the original question as completely as possible given all evidence so far. Remove redundancy, resolve contradictions, and maintain logical flow. Keep source URLs as inline citations where relevant.

Write only the updated report — no preamble or meta-commentary.
"#;

/// `STOP_PROMPT`.
const STOP_PROMPT: &str = r#"You are deciding whether a research report is comprehensive enough.

**Original question:** {question}

**Current report:**
{report}

**Rounds completed:** {round_num}

Based on the report so far, do we have enough information to answer the question comprehensively?  Consider:
- Are the key aspects of the question addressed?
- Are there obvious gaps or unanswered sub-questions?
- Is the evidence sufficient and from multiple sources?

Reply with ONLY "YES" or "NO" followed by a brief one-sentence reason.
Example: "YES — The report covers all major aspects with evidence from multiple sources."
Example: "NO — We still lack information about the economic impact."
"#;

/// `FINAL_REPORT_PROMPT`.
const FINAL_REPORT_PROMPT: &str = r#"Write a **long, detailed, comprehensive** research report answering this question:

**Question:** {question}

**All collected evidence and analysis:**
{report}

Requirements:
- Write at MINIMUM 1500 words — this should be a thorough, magazine-quality article
- Use clear ## headings and ### subheadings to organize into logical sections
- Each section should have multiple detailed paragraphs, not just bullet points
- Synthesize and analyze the information — explain WHY things matter, draw comparisons, provide context
- Include specific data points, numbers, and statistics from the evidence
- Include source URLs as inline citations [like this](url)
- Note where sources agree and where they disagree
- Add a brief executive summary at the top
- End with a clear conclusion that directly answers the question
- Write in an engaging, informative style — not dry or robotic
"#;

/// `CATEGORY_PROMPTS` — insertion order matters (`_classify_category` iterates
/// the keys and `valid` joins them), so an ordered map is used.
pub static CATEGORY_PROMPTS: Lazy<indexmap::IndexMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = indexmap::IndexMap::new();
    m.insert(
        "product",
        r#"IMPORTANT FORMAT OVERRIDE — this is a PRODUCT research report:
- Structure as a RANKED LIST of products/options (best first)
- For EACH product include: name as ### heading, approximate price, 2-3 sentence summary, **Pros:** bullet list, **Cons:** bullet list, **Where to buy:** URLs as links
- Start with a quick-compare markdown table of top picks (columns: Name, Price, Best For, Rating)
- End with a ## Verdict section picking Best Overall and Best Value
- Still include source citations inline"#,
    );
    m.insert(
        "comparison",
        r#"IMPORTANT FORMAT OVERRIDE — this is a COMPARISON report:
- Create a ## Comparison Table as a markdown table comparing ALL options across key criteria (rows = criteria, columns = options)
- Use checkmarks, ratings, or short values in cells
- Write a ## section per option with its strengths, weaknesses, and ideal use case
- End with ## Best For verdicts (e.g., "**Best for small teams:** Option A because...")
- Include a ## Shared Considerations section for things that apply to all options"#,
    );
    m.insert(
        "howto",
        r#"IMPORTANT FORMAT OVERRIDE — this is a HOW-TO guide:
- Start with ## Quick Guide — a super concise numbered list (one line per step, no details, just the action). Example: 1. Install X  2. Run Y  3. Configure Z
- Then ## Prerequisites listing what's needed before starting
- Then the detailed steps: ## Step 1: ..., ## Step 2: ...
- Each step should have a clear heading and detailed instructions
- Use blockquotes (> ) for tips and warnings: > **Tip:** ... or > **Warning:** ...
- End with ## Common Mistakes section
- Add estimated time and difficulty level near the top"#,
    );
    m.insert(
        "factcheck",
        r#"IMPORTANT FORMAT OVERRIDE — this is a FACT-CHECK report:
- Start with ## The Claim restating what's being checked
- Create ## Evidence For and ## Evidence Against sections
- Each piece of evidence should be a ### with source name, what it found, and how strong the evidence is
- Include a ## Verdict section with one of: **Supported**, **Mixed Evidence**, or **Unsupported**
- End with ## Nuance & Caveats for important context and limitations
- Be balanced and cite sources for every claim"#,
    );
    m
});

/// Replace a single `{key}` placeholder token everywhere it appears.
///
/// Manual substitution (not `format!`) so the literal `{{ }}` example JSON in
/// the templates is left untouched. Mirrors the subset of `str.format` the
/// Python actually relies on (named placeholders, no positional / nested).
fn fill(template: &str, key: &str, value: &str) -> String {
    template.replace(&format!("{{{key}}}"), value)
}

// ---------------------------------------------------------------------------
// DeepResearcher
// ---------------------------------------------------------------------------

/// Iterative research engine following the IterResearch pattern.
///
/// Each round: LLM generates queries -> search -> LLM extracts from top pages ->
/// LLM synthesizes into evolving report -> LLM decides continue/stop.
///
/// The search/fetch back-ends are wired to the real `crate::src::search` engine
/// (providers + content). When search is genuinely down or `disabled`, the
/// round loop still degrades to the "Search unavailable" branch — exactly as
/// Python does.
pub struct DeepResearcher {
    pub llm_endpoint: String,
    pub llm_model: String,
    pub llm_headers: Option<indexmap::IndexMap<String, String>>,
    pub search_provider_override: Option<String>,
    pub category: Option<String>,
    pub max_rounds: i64,
    pub max_time: i64,
    pub max_urls_per_round: i64,
    pub max_content_chars: usize,
    pub max_report_tokens: i64,
    pub min_rounds: i64,
    pub max_empty_rounds: i64,
    pub synthesis_window: usize,
    progress: Option<Arc<dyn Fn(Value) + Send + Sync>>,
    cancelled: Arc<AtomicBool>,
    start_time: f64,
    pub queries_used: HashSet<String>,
    pub urls_fetched: HashSet<String>,
    pub round_count: i64,
    /// Search providers that actually returned results, in arrival order —
    /// surfaced in the visual report and the public `get_stats` contract.
    pub providers_used: Vec<String>,
    pub findings: Vec<Value>,
    pub evolving_report: String,
    pub research_plan: String,
    /// Mirrors Python's lazily-set `self._last_search_error` (only assigned when
    /// a provider fails). `None` => "unknown error" in the error message.
    last_search_error: Option<String>,
}

/// A search result hit (`{"url", "title", ...}` in Python) — kept as a JSON
/// `Value` to ports 1:1 with the provider result dicts.
type SearchHit = Value;

/// The result of one `_search` call. Python mutates `self.providers_used` and
/// `self._last_search_error` directly inside `_search`; the Rust port runs the
/// per-query searches concurrently via `join_all` over `&self`, so each call
/// returns the would-be mutations here for `search_and_extract` (which holds the
/// `&mut self`) to apply after the fan-out completes.
#[derive(Default)]
struct SearchOutcome {
    hits: Vec<SearchHit>,
    /// First provider that returned a non-empty result (appended to
    /// `providers_used` if not already present).
    provider_used: Option<String>,
    /// Last per-provider failure string (`"prov: err"`), if any.
    last_error: Option<String>,
}

impl DeepResearcher {
    /// Construct with the Python constructor defaults for any field the caller
    /// does not override. Use `DeepResearcher::new(endpoint, model)` then the
    /// builder-style setters, mirroring Python keyword args.
    #[allow(clippy::too_many_arguments)]
    pub fn new(llm_endpoint: impl Into<String>, llm_model: impl Into<String>) -> Self {
        DeepResearcher {
            llm_endpoint: llm_endpoint.into(),
            llm_model: llm_model.into(),
            llm_headers: None,
            search_provider_override: None,
            category: None,
            max_rounds: 8,
            max_time: 300,
            max_urls_per_round: 3,
            max_content_chars: 15000,
            max_report_tokens: 8192,
            min_rounds: 2,
            max_empty_rounds: 2,
            synthesis_window: 10,
            progress: None,
            cancelled: Arc::new(AtomicBool::new(false)),
            start_time: 0.0,
            queries_used: HashSet::new(),
            urls_fetched: HashSet::new(),
            round_count: 0,
            providers_used: Vec::new(),
            findings: Vec::new(),
            evolving_report: String::new(),
            research_plan: String::new(),
            last_search_error: None,
        }
    }

    pub fn with_headers(mut self, headers: Option<indexmap::IndexMap<String, String>>) -> Self {
        self.llm_headers = headers;
        self
    }

    pub fn with_search_provider(mut self, provider: Option<String>) -> Self {
        self.search_provider_override = provider;
        self
    }

    pub fn with_category(mut self, category: Option<String>) -> Self {
        self.category = category;
        self
    }

    /// Register a progress callback (`progress_callback` kwarg). Receives the
    /// event dict as a `serde_json::Value` object.
    pub fn with_progress(mut self, cb: Arc<dyn Fn(Value) + Send + Sync>) -> Self {
        self.progress = Some(cb);
        self
    }

    /// Returns the cancellation handle so an external caller can request a stop.
    /// (Python keeps the flag on `self`; the `Arc<AtomicBool>` lets the handler
    /// cancel a `research()` future running on another task.)
    pub fn cancel_handle(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.cancelled)
    }

    /// Request cooperative cancellation of the research loop.
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    /// Run iterative research and return a final report.
    ///
    /// * `prior_report` — previous report to continue from (follow-up research).
    /// * `prior_findings` — previous findings to build on.
    /// * `prior_urls` — URLs already visited (won't be re-fetched).
    pub async fn research(
        &mut self,
        question: &str,
        prior_report: &str,
        prior_findings: Option<Vec<Value>>,
        prior_urls: Option<HashSet<String>>,
    ) -> String {
        self.start_time = pytime::time();
        let mut findings: Vec<Value> = prior_findings.unwrap_or_default();
        let mut report: String = prior_report.to_string();

        // PLAN: analyze the question and create a research strategy. Python has
        // two arms (fresh vs continuation) that differ only in the log line.
        if prior_report.is_empty() {
            self.emit_phase("planning");
            self.research_plan = self.create_plan(question).await;
            logger_info(&format!(
                "Research plan: {}",
                chars_take(&self.research_plan, 200)
            ));
        } else {
            self.emit_phase("planning");
            self.research_plan = self.create_plan(question).await;
            logger_info(&format!(
                "Continuation plan: {}",
                chars_take(&self.research_plan, 200)
            ));
        }
        if self.category.is_none() && prior_report.is_empty() {
            self.category = self.classify_category(question).await;
            if let Some(cat) = &self.category {
                logger_info(&format!("Auto-detected category: {cat}"));
            }
        }

        if let Some(urls) = prior_urls {
            self.urls_fetched.extend(urls);
        }
        // expose for handler. DEVIATION: Python aliases `self.findings` to the
        // same list object as the local `findings`, so it grows live during the
        // run; Rust cannot share a mutable owner, so we seed `self.findings`
        // here and re-assign it from the local at the end of `research()` (see
        // the final assignment below). The handler reads `self.findings` after
        // the run (or salvages `evolving_report` on timeout), where the two are
        // identical, so the observable contract is preserved.
        self.findings = findings.clone();
        let mut consecutive_empty_rounds: i64 = 0;

        let mut round_num: i64 = 1;
        while round_num <= self.max_rounds {
            self.round_count = round_num;
            if self.cancelled.load(Ordering::SeqCst) {
                logger_info(&format!("Research cancelled after {} rounds", round_num - 1));
                break;
            }
            if self.time_exceeded() {
                logger_info(&format!("Time limit reached after {} rounds", round_num - 1));
                break;
            }

            logger_info(&format!("=== Research Round {round_num} ==="));
            self.emit(event(&[
                ("phase", Value::from("searching")),
                ("round", Value::from(round_num)),
                ("total_sources", Value::from(self.urls_fetched.len())),
            ]));

            // THINK: generate queries
            let queries = self.generate_queries(question, &report, round_num).await;
            if queries.is_empty() {
                logger_warning(&format!("Round {round_num}: no queries generated, stopping"));
                break;
            }

            self.emit(event(&[
                ("phase", Value::from("searching")),
                ("round", Value::from(round_num)),
                ("queries", Value::from(queries.len())),
                ("query_preview", Value::from(queries.first().cloned().unwrap_or_default())),
                ("total_sources", Value::from(self.urls_fetched.len())),
            ]));

            // SEARCH + EXTRACT
            let round_findings = self.search_and_extract(&queries, question).await;
            if !round_findings.is_empty() {
                let n = round_findings.len();
                findings.extend(round_findings);
                consecutive_empty_rounds = 0;
                logger_info(&format!("Round {round_num}: extracted {n} findings"));
                self.emit(event(&[
                    ("phase", Value::from("reading")),
                    ("round", Value::from(round_num)),
                    ("new_sources", Value::from(n)),
                    ("total_sources", Value::from(self.urls_fetched.len())),
                    ("total_findings", Value::from(findings.len())),
                ]));
            } else {
                consecutive_empty_rounds += 1;
                logger_info(&format!(
                    "Round {round_num}: no new findings ({consecutive_empty_rounds} consecutive empty)"
                ));
                if consecutive_empty_rounds >= self.max_empty_rounds {
                    logger_warning(&format!(
                        "Search appears to be down — {} consecutive rounds with no results",
                        self.max_empty_rounds
                    ));
                    let err_detail = self
                        .last_search_error
                        .clone()
                        .unwrap_or_else(|| "unknown error".to_string());
                    self.emit(event(&[
                        ("phase", Value::from("error")),
                        ("message", Value::from(format!("Search engine unavailable: {err_detail}"))),
                    ]));
                    if findings.is_empty() {
                        return format!(
                            "**Search unavailable** — Web search failed after {round_num} rounds. \
                            Error: {err_detail}\n\nPlease check your search provider settings and \
                            ensure the service is running."
                        );
                    }
                    break;
                }
            }

            // SYNTHESIZE
            if !findings.is_empty() {
                self.emit(event(&[
                    ("phase", Value::from("analyzing")),
                    ("round", Value::from(round_num)),
                    ("total_sources", Value::from(self.urls_fetched.len())),
                    ("total_findings", Value::from(findings.len())),
                ]));
                report = self.synthesize(question, &findings, &report).await;
            }

            // DECIDE
            if round_num >= self.min_rounds {
                let should_stop = self.should_stop(question, &report, round_num).await;
                if should_stop {
                    logger_info(&format!("LLM decided to stop after round {round_num}"));
                    break;
                }
            }

            round_num += 1;
        }

        // FINAL REPORT
        self.emit(event(&[
            ("phase", Value::from("writing")),
            ("total_sources", Value::from(self.urls_fetched.len())),
            ("total_findings", Value::from(findings.len())),
        ]));
        if report.is_empty() {
            return "No information could be gathered for this question.".to_string();
        }

        self.evolving_report = report.clone(); // preserve pre-synthesis report
        let final_report = self.final_report(question, &report).await;
        let elapsed = pytime::time() - self.start_time;
        logger_info(&format!(
            "Research complete: {} rounds, {} findings, {} URLs, {:.1}s",
            self.round_count,
            findings.len(),
            self.urls_fetched.len(),
            elapsed
        ));
        self.findings = findings;
        final_report
    }

    // ------------------------------------------------------------------
    // LLM helper
    // ------------------------------------------------------------------

    /// Call the LLM asynchronously and strip thinking tags. Returns the empty
    /// string on failure (the callers all guard with try/except and fall back).
    async fn llm(&self, messages: Vec<Value>, temperature: f64, max_tokens: i64, timeout: u64) -> Result<String, String> {
        use crate::src::llm_core::llm_call_async;
        let headers = self.llm_headers.clone().unwrap_or_default();
        let response = llm_call_async(
            &self.llm_endpoint,
            &self.llm_model,
            messages,
            temperature,
            max_tokens,
            headers,
            timeout,
        )
        .await
        .map_err(|e| e.to_string())?;
        Ok(strip_thinking(Some(&response)).unwrap_or_default())
    }

    // ------------------------------------------------------------------
    // PLAN: create research strategy
    // ------------------------------------------------------------------

    /// LLM analyzes the question and creates a research plan.
    async fn create_plan(&mut self, question: &str) -> String {
        let prompt = fill(RESEARCH_PLAN_PROMPT, "question", question);
        match self
            .llm(vec![user_msg(&prompt)], 0.3, 1024, 30)
            .await
        {
            Ok(response) => {
                // Try to parse as JSON for structured plan
                if let Some(parsed) = Self::parse_json_object(&response) {
                    let mut parts: Vec<String> = Vec::new();
                    if let Some(arr) = parsed.get("sub_questions").and_then(json_str_list) {
                        if !arr.is_empty() {
                            parts.push(format!("Sub-questions: {}", arr.join("; ")));
                        }
                    }
                    if let Some(arr) = parsed.get("key_topics").and_then(json_str_list) {
                        if !arr.is_empty() {
                            parts.push(format!("Key topics: {}", arr.join(", ")));
                        }
                    }
                    if let Some(crit) = parsed.get("success_criteria").and_then(Value::as_str) {
                        if !crit.is_empty() {
                            parts.push(format!("Success: {crit}"));
                        }
                    }
                    if !parts.is_empty() {
                        return parts.join("\n");
                    }
                    return response;
                }
                response
            }
            Err(e) => {
                logger_warning(&format!("Research planning failed: {e}"));
                self.emit(event(&[
                    ("phase", Value::from("warning")),
                    ("message", Value::from("Planning step failed, proceeding with direct search")),
                ]));
                String::new()
            }
        }
    }

    /// Fast LLM call to classify the research question into a category.
    async fn classify_category(&self, question: &str) -> Option<String> {
        let valid: String = CATEGORY_PROMPTS.keys().copied().collect::<Vec<_>>().join(", ");
        let prompt = format!(
            "Classify this research question into exactly ONE category.\n\
            Categories: {valid}\n\
            If none fit well, respond with: general\n\n\
            Question: {question}\n\n\
            Respond with ONLY the category name, nothing else."
        );
        match self.llm(vec![user_msg(&prompt)], 0.0, 20, 15).await {
            Ok(result) => {
                let cat = result.trim().to_lowercase();
                // Clean one-word answer first.
                let first = cat
                    .split_whitespace()
                    .next()
                    .map(|w| w.trim_matches(|c| ".,\"'*:".contains(c)).to_string())
                    .unwrap_or_default();
                if CATEGORY_PROMPTS.contains_key(first.as_str()) {
                    return Some(first);
                }
                // Weak local models often wrap the label in preamble — scan the
                // whole reply for any known category word before giving up.
                for c in CATEGORY_PROMPTS.keys() {
                    if cat.contains(c) {
                        return Some((*c).to_string());
                    }
                }
                None
            }
            Err(e) => {
                logger_warning(&format!("Category classification failed: {e}"));
                None
            }
        }
    }

    // ------------------------------------------------------------------
    // THINK: generate search queries
    // ------------------------------------------------------------------

    async fn generate_queries(&mut self, question: &str, report: &str, round_num: i64) -> Vec<String> {
        let (num_queries, round_instruction) = if round_num == 1 {
            (
                4,
                "This is the first round — generate broad, diverse queries that explore the key facets of the question.",
            )
        } else {
            (
                3,
                "We already have partial findings.  Generate targeted follow-up queries to fill gaps, verify claims, or explore specific aspects that the report doesn't yet cover well.",
            )
        };

        let plan = if self.research_plan.is_empty() {
            "(No plan — search broadly.)".to_string()
        } else {
            self.research_plan.clone()
        };
        let report_txt = if report.is_empty() {
            "(No findings yet.)".to_string()
        } else {
            report.to_string()
        };

        let mut prompt = fill(QUERY_GEN_PROMPT, "question", question);
        prompt = fill(&prompt, "research_plan", &plan);
        prompt = fill(&prompt, "report", &report_txt);
        prompt = fill(&prompt, "round_num", &round_num.to_string());
        prompt = fill(&prompt, "num_queries", &num_queries.to_string());
        prompt = fill(&prompt, "round_instruction", round_instruction);

        match self.llm(vec![user_msg(&prompt)], 0.5, 4096, 60).await {
            Ok(response) => {
                let queries = Self::parse_json_array(&response);
                // Deduplicate against queries already used.
                let new_queries: Vec<String> = queries
                    .into_iter()
                    .filter(|q| !self.queries_used.contains(q))
                    .collect();
                for q in &new_queries {
                    self.queries_used.insert(q.clone());
                }
                logger_info(&format!("Round {round_num} queries: {new_queries:?}"));
                new_queries
            }
            Err(e) => {
                logger_error(&format!("Query generation failed: {e}"));
                self.emit(event(&[
                    ("phase", Value::from("warning")),
                    ("message", Value::from(format!("Query generation failed: {e}"))),
                ]));
                Vec::new()
            }
        }
    }

    // ------------------------------------------------------------------
    // SEARCH + EXTRACT
    // ------------------------------------------------------------------

    /// Search each query and extract relevant info from top results.
    async fn search_and_extract(&mut self, queries: &[String], question: &str) -> Vec<Value> {
        let mut all_findings: Vec<Value> = Vec::new();

        // Search all queries in parallel (Python `asyncio.gather`). Each call
        // returns a `SearchOutcome` carrying its hits plus the would-be
        // `self.providers_used` / `self._last_search_error` mutations, applied
        // below once the `&self` fan-out has joined.
        let search_tasks = queries.iter().map(|q| self.search(q));
        let search_results: Vec<SearchOutcome> =
            futures_util::future::join_all(search_tasks).await;

        // Apply the deferred `self` mutations in result order (Python mutates
        // these inline inside each `_search`).
        for outcome in &search_results {
            if let Some(prov) = &outcome.provider_used {
                if !self.providers_used.contains(prov) {
                    self.providers_used.push(prov.clone());
                }
            }
            if let Some(err) = &outcome.last_error {
                self.last_search_error = Some(err.clone());
            }
        }

        // Collect URLs to fetch from all search results. The Python `break`
        // inside the inner loop exits only that per-result loop (capping URLs
        // *per search result*), not the outer iteration over search_results.
        let cap = (self.max_urls_per_round.max(0) as usize) * queries.len();
        let mut urls_to_fetch: Vec<SearchHit> = Vec::new();
        for outcome in search_results {
            let hits = outcome.hits;
            if hits.is_empty() {
                continue;
            }
            for r in hits {
                let url = r.get("url").and_then(Value::as_str).unwrap_or("").to_string();
                if !url.is_empty() && !self.urls_fetched.contains(&url) {
                    urls_to_fetch.push(r);
                    self.urls_fetched.insert(url);
                }
                if urls_to_fetch.len() >= cap {
                    break;
                }
            }
        }

        if self.cancelled.load(Ordering::SeqCst) || self.time_exceeded() {
            return all_findings;
        }

        // Fetch and extract all URLs concurrently.
        let extract_tasks = urls_to_fetch.iter().map(|r| {
            let url = r.get("url").and_then(Value::as_str).unwrap_or("").to_string();
            let title = r.get("title").and_then(Value::as_str).unwrap_or("").to_string();
            self.fetch_and_extract(url, question, title)
        });
        let results_gathered: Vec<Result<Option<Value>, String>> =
            futures_util::future::join_all(extract_tasks).await;

        for result in results_gathered {
            match result {
                Err(e) => {
                    logger_warning(&format!("Extraction error: {e}"));
                    continue;
                }
                Ok(Some(finding)) => all_findings.push(finding),
                Ok(None) => {}
            }
        }

        all_findings
    }

    /// Run a search query using the configured research search provider.
    ///
    /// Mirrors Python `_search`: resolve the provider, short-circuit `disabled`,
    /// then walk the provider chain calling the sync-blocking
    /// `crate::src::search::_call_provider` inside `spawn_blocking` (the
    /// `asyncio.to_thread` analogue). The mutations Python performs on
    /// `self.providers_used` / `self._last_search_error` are returned via the
    /// `SearchOutcome` and applied by `search_and_extract` (which holds the
    /// `&mut self`), so the concurrent `join_all` fan-out can keep `&self`.
    async fn search(&self, query: &str) -> SearchOutcome {
        use crate::src::search::{_build_provider_chain, _call_provider, _get_search_settings};

        let mut outcome = SearchOutcome::default();

        let settings = _get_search_settings();
        let mut provider = self
            .search_provider_override
            .clone()
            .unwrap_or_default()
            .trim()
            .to_string();
        if provider.is_empty() {
            provider = settings
                .get("research_search_provider")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
        }
        if provider.is_empty() {
            provider = settings
                .get("search_provider")
                .and_then(Value::as_str)
                .unwrap_or("searxng")
                .to_string();
        }

        if provider == "disabled" {
            logger_info("Search is disabled for research");
            return outcome;
        }

        // Try primary provider, then fallbacks.
        for prov in _build_provider_chain(&provider) {
            let q = query.to_string();
            let prov_for_task = prov.clone();
            // results = await asyncio.to_thread(_call_provider, prov, query, 10)
            let join = tokio::task::spawn_blocking(move || {
                _call_provider(&prov_for_task, &q, 10, None)
            })
            .await;
            match join {
                Ok(results) => {
                    if !results.is_empty() {
                        logger_info(&format!(
                            "Research search: {} returned {} results",
                            prov,
                            results.len()
                        ));
                        outcome.provider_used = Some(prov);
                        outcome.hits = results;
                        return outcome;
                    }
                }
                Err(e) => {
                    // A panicked/cancelled blocking task is the Rust analogue of
                    // the provider raising — record it like Python's except branch.
                    logger_warning(&format!("Research search: {prov} failed: {e}"));
                    outcome.last_error = Some(format!("{prov}: {e}"));
                }
            }
        }
        outcome
    }

    /// Fetch a URL's content and use the LLM to extract relevant info.
    ///
    /// Mirrors Python `_fetch_and_extract`: emit `reading`, fetch the page via
    /// the sync-blocking `crate::src::search::fetch_webpage_content` inside
    /// `spawn_blocking`, bail on failure/empty content, truncate at a paragraph
    /// boundary, then run the LLM extraction.
    async fn fetch_and_extract(
        &self,
        url: String,
        question: &str,
        title: String,
    ) -> Result<Option<Value>, String> {
        let display = if title.is_empty() { url.clone() } else { title.clone() };
        self.emit(event(&[
            ("phase", Value::from("reading")),
            ("url", Value::from(url.clone())),
            ("title", Value::from(display)),
            ("total_sources", Value::from(self.urls_fetched.len())),
        ]));

        // page = await asyncio.to_thread(fetch_webpage_content, url, 10)
        let url_for_task = url.clone();
        let page = match tokio::task::spawn_blocking(move || {
            crate::src::search::fetch_webpage_content(&url_for_task, 10, 0)
        })
        .await
        {
            Ok(page) => page,
            Err(e) => {
                logger_warning(&format!("Failed to fetch {url}: {e}"));
                return Ok(None);
            }
        };

        let success = page.get("success").and_then(Value::as_bool).unwrap_or(false);
        let content_str = page.get("content").and_then(Value::as_str).unwrap_or("");
        if !success || content_str.is_empty() {
            return Ok(None);
        }

        // Truncate to avoid blowing up context, preferring a paragraph boundary.
        let mut content = content_str.to_string();
        if content.chars().count() > self.max_content_chars {
            let truncated: String = content.chars().take(self.max_content_chars).collect();
            // last_para = truncated.rfind('\n\n')
            if let Some(last_para) = truncated.rfind("\n\n") {
                // if last_para > self.max_content_chars * 0.8
                if (last_para as f64) > self.max_content_chars as f64 * 0.8 {
                    content = truncated[..last_para].to_string();
                } else {
                    content = truncated;
                }
            } else {
                content = truncated;
            }
        }

        // EXTRACTOR_PROMPT.format(webpage_content=content, goal=question) — the
        // doubled `{{ }}` example JSON collapses to single braces under
        // str.format, so we collapse them here after the named substitutions.
        let mut prompt = fill(EXTRACTOR_PROMPT, "webpage_content", &content);
        prompt = fill(&prompt, "goal", question);
        prompt = prompt.replace("{{", "{").replace("}}", "}");

        match self
            .llm(vec![user_msg(&prompt)], 0.2, 2048, 45)
            .await
        {
            Ok(response) => {
                let page_title = page.get("title").and_then(Value::as_str).unwrap_or("");
                let og_image = page.get("og_image").and_then(Value::as_str).unwrap_or("");
                if let Some(parsed) = Self::parse_json_object(&response) {
                    let mut obj = parsed;
                    obj.insert("url".to_string(), Value::from(url));
                    obj.insert(
                        "title".to_string(),
                        Value::from(if title.is_empty() { page_title.to_string() } else { title }),
                    );
                    obj.insert("og_image".to_string(), Value::from(og_image));
                    // Skip findings where the LLM says the page is useless.
                    let summary = obj.get("summary").and_then(Value::as_str).unwrap_or("");
                    if is_low_quality(summary) {
                        logger_info(&format!("Skipping low-quality extraction from {}", obj
                            .get("url")
                            .and_then(Value::as_str)
                            .unwrap_or("")));
                        return Ok(None);
                    }
                    return Ok(Some(Value::Object(obj)));
                }
                // If JSON parsing fails, treat entire response as evidence.
                let evidence: String = response.chars().take(3000).collect();
                let summary: String = response.chars().take(500).collect();
                let mut obj = Map::new();
                obj.insert("url".to_string(), Value::from(url));
                obj.insert(
                    "title".to_string(),
                    Value::from(if title.is_empty() { page_title.to_string() } else { title }),
                );
                obj.insert("og_image".to_string(), Value::from(og_image));
                obj.insert("rational".to_string(), Value::from("LLM extraction (raw)"));
                obj.insert("evidence".to_string(), Value::from(evidence));
                obj.insert("summary".to_string(), Value::from(summary));
                Ok(Some(Value::Object(obj)))
            }
            Err(e) => {
                logger_warning(&format!("LLM extraction failed for {url}: {e}"));
                Ok(None)
            }
        }
    }

    // ------------------------------------------------------------------
    // SYNTHESIZE
    // ------------------------------------------------------------------

    /// LLM synthesizes all findings into an updated report.
    async fn synthesize(&self, question: &str, findings: &[Value], current_report: &str) -> String {
        // Format findings for the prompt — last `synthesis_window` items.
        let window: Vec<Value> = if findings.len() > self.synthesis_window {
            logger_info(&format!(
                "Synthesis using last {} of {} findings",
                self.synthesis_window,
                findings.len()
            ));
            findings[findings.len() - self.synthesis_window..].to_vec()
        } else {
            findings.to_vec()
        };
        let findings_text = Self::format_findings(&window);

        let report_txt = if current_report.is_empty() {
            "(First round — no report yet.)".to_string()
        } else {
            current_report.to_string()
        };

        let mut prompt = fill(SYNTHESIZE_PROMPT, "question", question);
        prompt = fill(&prompt, "report", &report_txt);
        prompt = fill(&prompt, "new_findings", &findings_text);

        match self
            .llm(vec![user_msg(&prompt)], 0.3, self.max_report_tokens, 60)
            .await
        {
            Ok(updated) => updated,
            Err(e) => {
                logger_error(&format!("Synthesis failed: {e}"));
                self.emit(event(&[
                    ("phase", Value::from("warning")),
                    ("message", Value::from("Synthesis failed, keeping previous report")),
                ]));
                current_report.to_string() // keep the old report on failure
            }
        }
    }

    // ------------------------------------------------------------------
    // DECIDE
    // ------------------------------------------------------------------

    /// Let the LLM decide whether the report is comprehensive enough.
    async fn should_stop(&self, question: &str, report: &str, round_num: i64) -> bool {
        let mut prompt = fill(STOP_PROMPT, "question", question);
        prompt = fill(&prompt, "report", report);
        prompt = fill(&prompt, "round_num", &round_num.to_string());

        match self.llm(vec![user_msg(&prompt)], 0.1, 128, 60).await {
            Ok(response) => {
                // Reasoning models prepend a <think>...</think> block — strip it
                // before checking for YES/NO, otherwise the answer always looks
                // like it starts with "<THINK>" and the engine never stops.
                let clean = strip_thinking(Some(&response)).unwrap_or_default();
                let clean = clean.trim();
                // Tolerate "**YES**", "Yes.", quotes, etc.
                let answer = strip_leading_decoration(clean).to_uppercase();
                let should_stop = answer.starts_with("YES");
                logger_info(&format!("Stop decision (round {round_num}): {}", chars_take(clean, 120)));
                should_stop
            }
            Err(e) => {
                logger_warning(&format!("Stop decision failed: {e}"));
                false // continue on error
            }
        }
    }

    // ------------------------------------------------------------------
    // FINAL REPORT
    // ------------------------------------------------------------------

    /// LLM writes a polished final report, retrying if too short.
    async fn final_report(&self, question: &str, report: &str) -> String {
        let mut prompt = fill(FINAL_REPORT_PROMPT, "question", question);
        prompt = fill(&prompt, "report", report);
        let cat_extra = self
            .category
            .as_deref()
            .and_then(|c| CATEGORY_PROMPTS.get(c).copied())
            .unwrap_or("");
        if !cat_extra.is_empty() {
            prompt.push_str("\n\n");
            prompt.push_str(cat_extra);
        }

        match self
            .llm(vec![user_msg(&prompt)], 0.3, self.max_report_tokens, 180)
            .await
        {
            Ok(result) => {
                // If report is too short, ask the LLM to expand it.
                let result_words = word_count(&result);
                if result_words < 400 {
                    logger_info(&format!(
                        "Final report too short ({result_words} words), requesting expansion"
                    ));
                    self.emit(event(&[
                        ("phase", Value::from("writing")),
                        ("message", Value::from("Expanding report...")),
                    ]));
                    let expand_msgs = vec![
                        user_msg(&prompt),
                        assistant_msg(&result),
                        user_msg(
                            "This report is too brief. Please expand it significantly:\n\
                            - Add detailed paragraphs for each section (not just bullet points)\n\
                            - Include specific data, numbers, and comparisons from the evidence\n\
                            - Explain context and significance — don't just list facts\n\
                            - Use ## headings and ### subheadings\n\
                            - Target at least 1000 words\n\
                            Write the full expanded report now.",
                        ),
                    ];
                    match self
                        .llm(expand_msgs, 0.4, self.max_report_tokens, 180)
                        .await
                    {
                        Ok(expanded) => {
                            if word_count(&expanded) > result_words {
                                return expanded;
                            }
                            result
                        }
                        // Python: the expansion is inside the same try; a failure
                        // there falls to the outer except -> return `report`.
                        Err(e) => {
                            logger_error(&format!("Final report generation failed: {e}"));
                            report.to_string()
                        }
                    }
                } else {
                    result
                }
            }
            Err(e) => {
                logger_error(&format!("Final report generation failed: {e}"));
                report.to_string() // return the evolving report as-is
            }
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /// Send a progress event via the callback, if one is registered.
    fn emit(&self, kwargs: Value) {
        if let Some(cb) = &self.progress {
            // Python swallows any callback error; the closure type is infallible
            // here, so there is nothing to catch.
            cb(kwargs);
        }
    }

    /// Convenience for the many single-`phase` emits.
    fn emit_phase(&self, phase: &str) {
        self.emit(event(&[("phase", Value::from(phase))]));
    }

    fn time_exceeded(&self) -> bool {
        (pytime::time() - self.start_time) > self.max_time as f64
    }

    /// Strip markdown code-block fences (```json ... ```) if present.
    fn strip_code_block(text: &str) -> String {
        let mut text = text.trim().to_string();
        if text.starts_with("```") {
            // re.sub(r'^```(?:json)?\s*', '', text)
            text = STRIP_FENCE_HEAD.replace(&text, "").to_string();
            // re.sub(r'\s*```$', '', text)
            text = STRIP_FENCE_TAIL.replace(&text, "").to_string();
        }
        text.trim().to_string()
    }

    /// Extract a JSON array of strings from LLM output (three-tier).
    pub fn parse_json_array(text: &str) -> Vec<String> {
        let text = Self::strip_code_block(text);

        // Tier 1: direct parse.
        if let Ok(parsed) = serde_json::from_str::<Value>(&text) {
            if let Some(arr) = parsed.as_array() {
                return arr.iter().map(value_to_py_str).collect();
            }
        }

        // Tier 2: greedy match to capture the full outermost array.
        if let Some(m) = ARRAY_RE.find(&text) {
            if let Ok(parsed) = serde_json::from_str::<Value>(m.as_str()) {
                if let Some(arr) = parsed.as_array() {
                    return arr.iter().map(value_to_py_str).collect();
                }
            }
        }

        // Tier 3: repair truncated arrays — find the last complete quoted string.
        if let Some(arr_start) = text.find('[') {
            let fragment = &text[arr_start..];
            let complete_items: Vec<String> = QUOTED_RE
                .captures_iter(fragment)
                .map(|c| c[1].to_string())
                .collect();
            if !complete_items.is_empty() {
                logger_info(&format!(
                    "Repaired truncated JSON array: recovered {} items",
                    complete_items.len()
                ));
                return complete_items;
            }
        }

        logger_warning(&format!("Could not parse JSON array from: {}", chars_take(&text, 200)));
        Vec::new()
    }

    /// Extract a JSON object from LLM output.
    pub fn parse_json_object(text: &str) -> Option<Map<String, Value>> {
        let text = Self::strip_code_block(text);

        if let Ok(Value::Object(m)) = serde_json::from_str::<Value>(&text) {
            return Some(m);
        }

        // Greedy match to capture the full outermost object.
        if let Some(m) = OBJECT_RE.find(&text) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(m.as_str()) {
                return Some(map);
            }
        }

        None
    }

    /// Format findings list into readable text for the synthesis prompt.
    fn format_findings(findings: &[Value]) -> String {
        let mut parts: Vec<String> = Vec::new();
        for (idx, f) in findings.iter().enumerate() {
            let i = idx + 1;
            let url = f.get("url").and_then(Value::as_str).unwrap_or("unknown");
            let title = f.get("title").and_then(Value::as_str).unwrap_or("");
            let summary = f.get("summary").and_then(Value::as_str).unwrap_or("");
            let evidence = f.get("evidence").and_then(Value::as_str).unwrap_or("");
            // Use summary if available, fall back to truncated evidence.
            let content: String = if !summary.is_empty() {
                summary.to_string()
            } else if !evidence.is_empty() {
                chars_take(evidence, 1000)
            } else {
                "(no content)".to_string()
            };
            parts.push(format!("**Finding {i}** — [{title}]({url})\n{content}"));
        }
        parts.join("\n\n")
    }

    /// Return research statistics (ordered like the Python dict insertion).
    pub fn get_stats(&self) -> indexmap::IndexMap<String, Value> {
        let elapsed = if self.start_time != 0.0 {
            pytime::time() - self.start_time
        } else {
            0.0
        };
        let mut stats: indexmap::IndexMap<String, Value> = indexmap::IndexMap::new();
        stats.insert("Duration".to_string(), Value::from(format!("{elapsed:.1}s")));
        stats.insert("Rounds".to_string(), Value::from(self.round_count));
        stats.insert("Queries".to_string(), Value::from(self.queries_used.len()));
        stats.insert("URLs".to_string(), Value::from(self.urls_fetched.len()));
        stats.insert("Model".to_string(), Value::from(self.llm_model.clone()));
        if !self.providers_used.is_empty() {
            stats.insert("Search".to_string(), Value::from(self.providers_used.join(", ")));
        }
        if let Some(cat) = &self.category {
            stats.insert("Category".to_string(), Value::from(capitalize(cat)));
        }
        stats
    }
}

// ---------------------------------------------------------------------------
// Regex statics (compiled once)
// ---------------------------------------------------------------------------

static STRIP_FENCE_HEAD: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"^```(?:json)?\s*").unwrap());
static STRIP_FENCE_TAIL: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"\s*```$").unwrap());
/// Greedy `\[[\s\S]*\]` — `(?s)` makes `.` match newlines so `.*` == `[\s\S]*`.
static ARRAY_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"(?s)\[.*\]").unwrap());
/// Greedy `\{[\s\S]*\}`.
static OBJECT_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"(?s)\{.*\}").unwrap());
/// `"([^"]*)"` — used to recover complete quoted strings from truncated arrays.
static QUOTED_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r#""([^"]*)""#).unwrap());
/// `^[\s*_`"'>#\-]+` — leading decoration before the YES/NO answer.
static LEADING_DECOR_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r#"^[\s*_`"'>#\-]+"#).unwrap());

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

fn logger_info(msg: &str) {
    crate::pylog::info(msg);
}
fn logger_warning(msg: &str) {
    crate::pylog::warning(msg);
}
fn logger_error(msg: &str) {
    crate::pylog::error(msg);
}

/// Build a `{"role": "user", "content": ...}` chat message.
fn user_msg(content: &str) -> Value {
    serde_json::json!({"role": "user", "content": content})
}

/// Build a `{"role": "assistant", "content": ...}` chat message.
fn assistant_msg(content: &str) -> Value {
    serde_json::json!({"role": "assistant", "content": content})
}

/// Assemble a progress-event object preserving key insertion order.
fn event(pairs: &[(&str, Value)]) -> Value {
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert((*k).to_string(), v.clone());
    }
    Value::Object(m)
}

/// `text[:n]` on Unicode code points (Python str slicing is codepoint-based).
fn chars_take(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python's `len(text.split())` — whitespace-split word count.
fn word_count(s: &str) -> usize {
    s.split_whitespace().count()
}

/// `re.sub(r'^[\s*_`"\'>#\-]+', '', clean).upper()` (the caller applies upper).
fn strip_leading_decoration(s: &str) -> String {
    LEADING_DECOR_RE.replace(s, "").to_string()
}

/// `str.capitalize()` — first char upper, the rest lower.
fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase(),
    }
}

/// `str(item)` faithfully: JSON strings render without quotes; everything else
/// uses its JSON serialisation (the closest stable analogue for the values an
/// LLM emits in a query array — almost always strings).
fn value_to_py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Coerce a JSON value that should be a list of strings (`sub_questions` /
/// `key_topics`) into `Vec<String>` via Python-`str()` semantics on each item.
fn json_str_list(v: &Value) -> Option<Vec<String>> {
    v.as_array().map(|arr| arr.iter().map(value_to_py_str).collect())
}

// ---------------------------------------------------------------------------
// Tests — lock the pure parse/format/stat helpers (no Python tests exist).
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_json_array_direct() {
        let out = DeepResearcher::parse_json_array(r#"["a", "b", "c"]"#);
        assert_eq!(out, vec!["a", "b", "c"]);
    }

    #[test]
    fn parse_json_array_code_fenced() {
        let out = DeepResearcher::parse_json_array("```json\n[\"x\", \"y\"]\n```");
        assert_eq!(out, vec!["x", "y"]);
    }

    #[test]
    fn parse_json_array_embedded_greedy() {
        let out =
            DeepResearcher::parse_json_array("Here you go:\n[\"one\", \"two\"]\nthanks");
        assert_eq!(out, vec!["one", "two"]);
    }

    #[test]
    fn parse_json_array_truncated_repair() {
        // No closing bracket -> tier 3 recovers complete quoted strings only.
        let out =
            DeepResearcher::parse_json_array("[\"query one\", \"query two\", \"query thr");
        assert_eq!(out, vec!["query one", "query two"]);
    }

    #[test]
    fn parse_json_array_non_string_items_stringified() {
        let out = DeepResearcher::parse_json_array("[1, 2, 3]");
        assert_eq!(out, vec!["1", "2", "3"]);
    }

    #[test]
    fn parse_json_object_basic() {
        let m = DeepResearcher::parse_json_object(r#"{"a": 1, "b": "x"}"#).unwrap();
        assert_eq!(m.get("a").and_then(Value::as_i64), Some(1));
        assert_eq!(m.get("b").and_then(Value::as_str), Some("x"));
    }

    #[test]
    fn parse_json_object_embedded() {
        let m =
            DeepResearcher::parse_json_object("prefix {\"k\": \"v\"} suffix").unwrap();
        assert_eq!(m.get("k").and_then(Value::as_str), Some("v"));
    }

    #[test]
    fn parse_json_object_none_on_garbage() {
        assert!(DeepResearcher::parse_json_object("not json at all").is_none());
    }

    #[test]
    fn format_findings_uses_summary_then_evidence() {
        let findings = vec![
            serde_json::json!({"url": "u1", "title": "T1", "summary": "S1"}),
            serde_json::json!({"url": "u2", "title": "T2", "evidence": "E2"}),
            serde_json::json!({"url": "u3", "title": "T3"}),
        ];
        let out = DeepResearcher::format_findings(&findings);
        assert!(out.contains("**Finding 1** — [T1](u1)\nS1"));
        assert!(out.contains("**Finding 2** — [T2](u2)\nE2"));
        assert!(out.contains("**Finding 3** — [T3](u3)\n(no content)"));
    }

    #[test]
    fn format_findings_default_url_is_unknown() {
        let findings = vec![serde_json::json!({"title": "T", "summary": "S"})];
        let out = DeepResearcher::format_findings(&findings);
        assert!(out.contains("[T](unknown)"));
    }

    #[test]
    fn strip_code_block_handles_plain_and_json_fence() {
        assert_eq!(DeepResearcher::strip_code_block("```json\n{}\n```"), "{}");
        assert_eq!(DeepResearcher::strip_code_block("```\nhi\n```"), "hi");
        assert_eq!(DeepResearcher::strip_code_block("  plain  "), "plain");
    }

    #[test]
    fn leading_decoration_then_yes() {
        let answer = strip_leading_decoration("**YES** — done").to_uppercase();
        assert!(answer.starts_with("YES"));
        let no = strip_leading_decoration("> NO — gaps remain").to_uppercase();
        assert!(no.starts_with("NO"));
    }

    #[test]
    fn capitalize_matches_python() {
        assert_eq!(capitalize("product"), "Product");
        assert_eq!(capitalize("HOWTO"), "Howto");
        assert_eq!(capitalize(""), "");
    }

    #[test]
    fn category_prompts_order_and_keys() {
        let keys: Vec<&str> = CATEGORY_PROMPTS.keys().copied().collect();
        assert_eq!(keys, vec!["product", "comparison", "howto", "factcheck"]);
    }

    #[test]
    fn fill_only_touches_named_token_not_double_braces() {
        let out = fill(RESEARCH_PLAN_PROMPT, "question", "What is X?");
        assert!(out.contains("**Question:** What is X?"));
        // The literal example JSON braces are doubled in the template and must
        // survive substitution untouched.
        assert!(out.contains("{{"));
        assert!(out.contains("\"sub_questions\""));
    }

    #[test]
    fn get_stats_shape() {
        let mut r = DeepResearcher::new("http://x", "model-y");
        r.round_count = 3;
        r.queries_used.insert("q1".to_string());
        r.urls_fetched.insert("u1".to_string());
        r.category = Some("product".to_string());
        let stats = r.get_stats();
        let keys: Vec<&String> = stats.keys().collect();
        assert_eq!(keys[0], "Duration");
        assert_eq!(stats.get("Rounds").and_then(Value::as_i64), Some(3));
        assert_eq!(stats.get("Model").and_then(Value::as_str), Some("model-y"));
        assert_eq!(stats.get("Category").and_then(Value::as_str), Some("Product"));
        // providers_used empty -> no "Search" key.
        assert!(stats.get("Search").is_none());
    }
}
