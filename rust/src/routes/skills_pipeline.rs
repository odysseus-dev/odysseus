// routes/skills_pipeline.rs  <- routes/skills_routes.py (the test/audit/improve orchestration)
//! The skills **self-improvement loop**: test → judge → self-edit → retry →
//! teacher → flag, plus the scheduled nightly audit. This is the orchestration
//! layer that `routes::skills_routes`'s HTTP endpoints, `src::builtin_actions`'s
//! `action_test_skills`/`action_audit_skills`, and `web::run`'s nightly-audit
//! loop all call into.
//!
//! Ported in full from `routes/skills_routes.py` (lines 77-1056):
//!   * `_skill_test_task` (pure task builder)
//!   * `_eval_skill_run` / `_eval_skill_necessity` / `_eval_skill_retrieval_precision`
//!     / `_should_check_retrieval_precision` — the LLM-as-judge helpers
//!   * the two module-global in-memory job stores `_skill_test_jobs` /
//!     `_skill_audit_jobs`
//!   * `_run_skill_test_job` / `_run_skill_test_once` — the agent run + judge
//!   * `_improve_skill_md` — the LLM rewrite
//!   * `_audit_one_skill` + helpers (`_apply_skill_md`, `_audit_finalize_status`,
//!     `_audit_auto_publish_policy`, `_skill_duplicate_blocker`,
//!     `_audit_generic_blocker`, `_audit_flag_text`)
//!   * `_run_audit_all_job` / `_resolve_audit_models` / `run_scheduled_skill_audit`
//!
//! ## Reused (NOT reimplemented) hard dependencies
//!   * [`crate::src::agent_loop::stream_agent_loop`] — the streaming agent run.
//!   * [`crate::src::endpoint_resolver::resolve_endpoint`] — worker model resolve.
//!   * [`crate::src::ai_interaction::resolve_model_spec`] (Python `_resolve_model`)
//!     — teacher model resolve.
//!   * [`crate::src::llm_core::llm_call_async`] — the non-streaming judge/rewrite
//!     calls.
//!   * [`crate::services::memory::skills::SkillsManager`] — skill CRUD + the audit
//!     sidecar.
//!
//! ## Documented divergences
//!   * **Model-drift normalization via `list_model_ids` is NOT applied.** The Rust
//!     [`crate::src::llm_core::list_model_ids`] is a different, network-free
//!     string-normalizer (it does NOT `GET /models`), so `_resolve_audit_models`
//!     uses the resolved model verbatim — byte-equivalent to the Python `except`
//!     branch where `_avail = []` so no served-model swap is applied.
//!   * **Audit cancellation is the cooperative `cancel` flag only.** Rust has no
//!     `asyncio.Task.cancel()` analog, so no JoinHandle is stored in the job and
//!     the in-flight skill finishes before the loop notices the flag at the next
//!     iteration boundary. The user-visible status still flips to `"cancelled"`
//!     immediately (the cancel endpoint sets it).
//!   * **The `stream_agent_loop` collect-loop's `try/except` arm never fires.** The
//!     Rust stream yields no error item, so the Python `except Exception as e:
//!     log.append({type: error, error: str(e)})` arm has no Rust source; the
//!     structure is preserved faithfully (a defensive `evaluating` log entry is
//!     always appended next), but no in-loop error can occur here.

use std::collections::HashMap;
use std::sync::Mutex;

use indexmap::IndexMap;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};

use crate::pylog as logger;
use crate::services::memory::skills::SkillsManager;

type Headers = IndexMap<String, String>;
type Teacher = (String, String, Headers);

// ===========================================================================
// Module-global in-memory job stores
// ===========================================================================
//
// `_skill_test_jobs: dict` keyed by `(owner, skill_name)` and
// `_skill_audit_jobs: dict` keyed by `(owner,)`. Both jobs are
// `serde_json::Map<String, Value>` (the dict) so the status endpoints can clone
// + reshape them with the same keys Python reads, and the pipelines mutate them
// in place under the lock.
//
// LOCK DISCIPLINE: the `std::sync::Mutex` guard is NEVER held across an `.await`.
// The long-running pipelines lock → read/clone or mutate one field → drop the
// guard → await. The append-as-you-go log locks per entry.

/// `_skill_test_jobs` — keyed `"{owner}\x00{name}"`.
static SKILL_TEST_JOBS: Lazy<Mutex<HashMap<String, Value>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// `_skill_audit_jobs` — keyed `"{owner}"`.
static SKILL_AUDIT_JOBS: Lazy<Mutex<HashMap<String, Value>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// `(user or "", name)` dict key. NUL separator can't collide with a slug/owner.
pub(crate) fn test_key(owner: Option<&str>, name: &str) -> String {
    format!("{}\u{0}{}", owner.unwrap_or(""), name)
}

/// `(user or "",)` single-element tuple key == just the owner.
pub(crate) fn audit_key(owner: Option<&str>) -> String {
    owner.unwrap_or("").to_string()
}

/// Lock the test store, apply `f` to the job at `key` if present, drop the guard.
fn with_test_job<R>(key: &str, f: impl FnOnce(&mut Map<String, Value>) -> R) -> Option<R> {
    let mut guard = SKILL_TEST_JOBS.lock().expect("SKILL_TEST_JOBS poisoned");
    match guard.get_mut(key) {
        Some(Value::Object(m)) => Some(f(m)),
        _ => None,
    }
}

/// Lock the audit store, apply `f` to the job at `key` if present, drop the guard.
fn with_audit_job<R>(key: &str, f: impl FnOnce(&mut Map<String, Value>) -> R) -> Option<R> {
    let mut guard = SKILL_AUDIT_JOBS.lock().expect("SKILL_AUDIT_JOBS poisoned");
    match guard.get_mut(key) {
        Some(Value::Object(m)) => Some(f(m)),
        _ => None,
    }
}

/// `_skill_test_jobs.get(key)` — clone the current test-job snapshot (the status
/// endpoint reshapes it).
pub(crate) fn get_test_job(key: &str) -> Option<Value> {
    SKILL_TEST_JOBS
        .lock()
        .expect("SKILL_TEST_JOBS poisoned")
        .get(key)
        .cloned()
}

/// `_skill_audit_jobs.get(key)` — clone the current audit-job snapshot.
pub(crate) fn get_audit_job(key: &str) -> Option<Value> {
    SKILL_AUDIT_JOBS
        .lock()
        .expect("SKILL_AUDIT_JOBS poisoned")
        .get(key)
        .cloned()
}

/// Seed `_skill_test_jobs[key]` (skills_routes.py:1305-1313).
pub(crate) fn seed_test_job(key: &str, name: &str, task: &str, model: &str) {
    let job = json!({
        "status": "running",
        "task": task,
        "model": model,
        "skill": name,
        "started": crate::pytime::time(),
        "log": [{"type": "skill_test_start", "task": task, "skill": name, "model": model}],
        "verdict": Value::Null,
    });
    SKILL_TEST_JOBS
        .lock()
        .expect("SKILL_TEST_JOBS poisoned")
        .insert(key.to_string(), job);
}

/// The new-audit-job guard: if `_skill_audit_jobs[key]` exists and is `"running"`,
/// return its snapshot (skills_routes.py:1354-1359 / 1026-1029). Otherwise None.
pub(crate) fn audit_running(key: &str) -> Option<Value> {
    let guard = SKILL_AUDIT_JOBS.lock().expect("SKILL_AUDIT_JOBS poisoned");
    match guard.get(key) {
        Some(Value::Object(m))
            if m.get("status").and_then(|v| v.as_str()) == Some("running") =>
        {
            Some(Value::Object(m.clone()))
        }
        _ => None,
    }
}

/// Seed `_skill_audit_jobs[key]` (skills_routes.py:1399-1405 / 1045-1052).
/// `teacher` is the teacher MODEL id (Python `teacher[1] if teacher else None`).
pub(crate) fn seed_audit_job(
    key: &str,
    scope: &str,
    model: &str,
    teacher: Option<&str>,
    names: &[String],
) {
    let teacher_suffix = match teacher {
        Some(t) => format!("; teacher {t}"),
        None => String::new(),
    };
    let log_line = format!("Auditing {} skill(s) with {model}{teacher_suffix}", names.len());
    let job = json!({
        "status": "running",
        "scope": scope,
        "model": model,
        "teacher": teacher,
        "total": names.len(),
        "done": 0,
        "current": Value::Null,
        "results": [],
        "log": [log_line],
        "started": crate::pytime::time(),
        "cancel": false,
    });
    SKILL_AUDIT_JOBS
        .lock()
        .expect("SKILL_AUDIT_JOBS poisoned")
        .insert(key.to_string(), job);
}

/// `audit_cancel`: set `cancel=true`, `status="cancelled"`, `current=None` on the
/// job at `key`. Returns true if a job existed (skills_routes.py:1424-1435).
pub(crate) fn cancel_audit_job(key: &str) -> bool {
    with_audit_job(key, |job| {
        job.insert("cancel".to_string(), Value::Bool(true));
        job.insert("status".to_string(), Value::String("cancelled".to_string()));
        job.insert("current".to_string(), Value::Null);
    })
    .is_some()
}

// ===========================================================================
// Small value/string helpers (the dynamic `dict.get` + `str(...)` calls)
// ===========================================================================

/// `str(v or "")` — Python str-coercion through truthiness (None/false/0/"" -> "").
fn str_or_empty(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::Bool(false)) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => {
            // `0 or ""` is falsy in Python; we only ever feed strings here in
            // practice, but mirror the `... or ""` shape for numbers too.
            if n.as_f64() == Some(0.0) {
                String::new()
            } else {
                n.to_string()
            }
        }
        Some(v) => v.to_string(),
    }
}

/// `str(v)` — plain Python str() (no truthiness collapse). Used for the `issues`
/// list items where Python does `str(x)[:200]`.
fn plain_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

/// Python `s[:n]` over codepoints (NOT bytes).
fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python `s[-n:]` over codepoints.
fn clip_tail(s: &str, n: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    let start = chars.len().saturating_sub(n);
    chars[start..].iter().collect()
}

/// `s.get(key)` -> owned string for a skill dict ("" when missing/non-string).
fn skill_str(s: &Map<String, Value>, key: &str) -> String {
    match s.get(key) {
        Some(Value::String(v)) => v.clone(),
        _ => String::new(),
    }
}

/// `s.get("name")` of a skill record (the canonical identity).
fn skill_name(s: &Map<String, Value>) -> String {
    skill_str(s, "name")
}

/// `next((s for s in skills if s.get("name") == name), None)`.
fn find_by_name<'a>(skills: &'a [Map<String, Value>], name: &str) -> Option<&'a Map<String, Value>> {
    skills.iter().find(|s| skill_name(s) == name)
}

/// `[str(x) for x in (d.get(key) or [])]` — string list from an optional JSON array.
fn str_list(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(a)) => a.iter().map(plain_str).collect(),
        _ => Vec::new(),
    }
}

// ===========================================================================
// Lenient-JSON / <think>-stripping regexes (Python re.I + [\s\S] = (?si))
// ===========================================================================

/// `<think>...</think>` (closed), case-insensitive, dot-all. Matches `<thinking>`.
static THINK_CLOSED_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?si)<think(?:ing)?>.*?</think(?:ing)?>").unwrap());
/// `<think>...$` (opened, never closed) to end of text.
static THINK_OPEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?si)<think(?:ing)?>.*$").unwrap());
/// A lone `</think>` (improve-md only strips this too).
static THINK_LONE_CLOSE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?si)</think(?:ing)?>").unwrap());
/// Every non-greedy balanced `{...}` candidate.
static BRACE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?s)\{.*?\}").unwrap());
/// Trailing comma before `}`/`]` (lenient JSON cleanup).
static TRAILING_COMMA_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r",(\s*[}\]])").unwrap());
/// Verdict keyword pulled straight from prose.
static VERDICT_KW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?si)verdict["'\s:]*\s*["']?(pass|needs_work|fail|inconclusive)"#).unwrap()
});
/// A frontmatter doc-start line `^---\s*\n` (multiline). The Python pattern is
/// `(?m)^---\s*\n(?=[\s\S]*?^name\s*:)`; the `regex` crate has NO lookahead, so
/// the `(?=...^name:)` assertion is checked separately by [`has_name_line_ahead`].
static FRONTMATTER_START_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^---\s*\n").unwrap());
/// `^name\s*:` line-start (the lookahead body the Python pattern asserts).
static NAME_LINE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^name\s*:").unwrap());

/// Reproduce `(?=[\s\S]*?^name\s*:)`: is there a `^name:` line anywhere at or
/// after `from` in `text`?
fn has_name_line_ahead(text: &str, from: usize) -> bool {
    NAME_LINE_RE.is_match(&text[from..])
}

/// `_re.sub(r'<think...>...</think>', '', text)` then `<think...>...$`, then `.strip()`.
fn strip_think(raw: &str) -> String {
    let t = THINK_CLOSED_RE.replace_all(raw, "");
    let t = THINK_OPEN_RE.replace_all(&t, "");
    t.trim().to_string()
}

/// Drop a trailing comma before `}` / `]` (Python `re.sub(r',(\s*[}\]])', r'\1', frag)`).
fn strip_trailing_commas(frag: &str) -> String {
    TRAILING_COMMA_RE.replace_all(frag, "${1}").to_string()
}

const VERDICTS: [&str; 4] = ["pass", "needs_work", "fail", "inconclusive"];

// ===========================================================================
// _skill_test_task (skills_routes.py:77-89, pure)
// ===========================================================================

/// `_skill_test_task(skill)` — build a self-contained test task string.
pub(crate) fn skill_test_task(skill: &Map<String, Value>) -> String {
    // `(skill.get("when_to_use") or skill.get("description") or skill.get("name") or "").strip()`
    let ctx_raw = {
        let w = skill_str(skill, "when_to_use");
        if !w.is_empty() {
            w
        } else {
            let d = skill_str(skill, "description");
            if !d.is_empty() {
                d
            } else {
                skill_str(skill, "name")
            }
        }
    };
    let ctx = ctx_raw.trim();
    let ctx = if ctx.is_empty() { "(general)" } else { ctx };
    format!(
        "Test this skill end-to-end. FIRST, set up a small realistic scenario it \
applies to — create any sample input it needs (e.g. a short document, a \
note, sample data). Do NOT ask the user for input; invent a plausible \
example yourself. THEN apply the skill fully to that example and show the \
result. Context for when this skill is used: {ctx}"
    )
}

// ===========================================================================
// _eval_skill_run (skills_routes.py:92-243) — LLM-as-judge
// ===========================================================================

/// `_clip(t, limit=24000)` — keep the TAIL (the result lives at the end) plus a
/// bit of the head when the transcript must be trimmed. Codepoint-based.
fn clip_transcript(t: &str, limit: usize) -> String {
    let t = t.trim();
    let t = if t.is_empty() { "(no output produced)" } else { t };
    if t.chars().count() <= limit {
        return t.to_string();
    }
    let head = limit / 4;
    format!(
        "{}\n\n…[transcript trimmed for length]…\n\n{}",
        clip(t, head),
        clip_tail(t, limit - head),
    )
}

/// `_parse(raw)` (skills_routes.py:147-207) — return the verdict dict or None.
fn parse_verdict(raw: &str) -> Option<Map<String, Value>> {
    let text = strip_think(raw);

    // `_coerce(d)`: keep only dicts carrying "verdict".
    fn coerce(v: Value) -> Option<Map<String, Value>> {
        match v {
            Value::Object(m) if m.contains_key("verdict") => Some(m),
            _ => None,
        }
    }

    let mut data: Option<Map<String, Value>> = None;
    // Scan every balanced {...} and keep the LAST that parses AND has "verdict".
    for m in BRACE_RE.find_iter(&text) {
        let frag = m.as_str();
        for cand in [frag.to_string(), strip_trailing_commas(frag)] {
            if let Ok(v) = serde_json::from_str::<Value>(&cand) {
                if let Some(d) = coerce(v) {
                    data = Some(d);
                }
            }
        }
    }
    // Fallback: greedy outermost `text.find('{')..text.rfind('}')`.
    if data.is_none() {
        if let (Some(a), Some(b)) = (text.find('{'), text.rfind('}')) {
            if b > a {
                let frag = &text[a..=b];
                for cand in [frag.to_string(), strip_trailing_commas(frag)] {
                    if let Ok(v) = serde_json::from_str::<Value>(&cand) {
                        if let Some(d) = coerce(v) {
                            data = Some(d);
                            break;
                        }
                    }
                }
            }
        }
    }

    // `v = str(data.get("verdict", "")).lower().strip()` (or None when no dict).
    let mut v: Option<String> = data.as_ref().map(|d| {
        str_or_empty(d.get("verdict")).to_lowercase().trim().to_string()
    });

    // Last resort: pull the verdict keyword straight from the prose.
    let valid = |s: &Option<String>| s.as_deref().is_some_and(|x| VERDICTS.contains(&x));
    if !valid(&v) {
        if let Some(cap) = VERDICT_KW_RE.captures(&text) {
            v = Some(cap[1].to_lowercase());
            if data.is_none() {
                data = Some(Map::new());
            }
        }
    }

    let data = data?;
    let v = v?;
    if !VERDICTS.contains(&v.as_str()) {
        return None;
    }

    // `conf = float(data.get("confidence", 0))` -> clamp 0..1 (non-numeric -> 0).
    let conf = match data.get("confidence") {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(0.0),
        Some(Value::Bool(true)) => 1.0,
        _ => 0.0,
    };
    let conf = conf.clamp(0.0, 1.0);

    let summary = clip(&str_or_empty(data.get("summary")), 400);
    // `[str(x)[:200] for x in (data.get("issues") or []) if str(x).strip()][:8]`
    let issues: Vec<Value> = str_list(data.get("issues"))
        .into_iter()
        .filter(|x| !x.trim().is_empty())
        .map(|x| Value::String(clip(&x, 200)))
        .take(8)
        .collect();

    let mut out = Map::new();
    out.insert("verdict".to_string(), Value::String(v));
    out.insert("confidence".to_string(), json!(conf));
    out.insert("summary".to_string(), Value::String(summary));
    out.insert("issues".to_string(), Value::Array(issues));
    Some(out)
}

/// `_eval_skill_run(skill_md, task, transcript, url, model, headers)` — grade a
/// run from its transcript. Returns the verdict dict (with `verdict="unknown"`
/// on failure).
pub(crate) async fn eval_skill_run(
    skill_md: &str,
    task: &str,
    transcript: &str,
    url: &str,
    model: &str,
    headers: &Headers,
) -> Map<String, Value> {
    let sys_prompt = "You are a strict QA reviewer judging whether an AI 'skill' (a reusable \
procedure) actually works. You are given the SKILL, the TASK it was tested \
on, and the TRANSCRIPT of the agent's run.\n\n\
Judge honestly:\n\
- Did following the skill accomplish the task?\n\
- Are the steps clear, correct, and reproducible?\n\
- Did it reference tools/commands that don't exist or that errored?\n\
- Is it too vague or generic to be a useful, reusable skill?\n\
- METADATA: do the frontmatter fields match what the skill actually does? \
Flag wrong/misleading/missing tags, a wrong category, a when_to_use that \
doesn't describe the real trigger, or a description that oversells or \
mismatches the body. List each metadata problem in 'issues' (prefix it \
with 'metadata:'). Metadata problems alone do NOT make the verdict 'fail' \
if the procedure works — note them as issues on an otherwise-passing run.\n\n\
IMPORTANT — fairness rule: if the run could NOT proceed because it lacked \
an input or target the test never provided (e.g. there was no document/\
email/data to act on, so the agent reasonably asked for it), that is NOT \
the skill's fault. Return verdict \"inconclusive\" — do NOT mark it fail \
or needs_work. Only judge the skill's PROCEDURE; reserve fail/needs_work \
for when the steps themselves are wrong, vague, or reference missing tools.\n\n\
If you need to reason, do it inside <think></think> FIRST. Then output \
ONLY this JSON (no fences):\n\
{\"verdict\": \"pass\" | \"needs_work\" | \"fail\" | \"inconclusive\", \
\"confidence\": 0.0-1.0, \"summary\": \"one short sentence\", \
\"issues\": [\"short issue\", ...]}";

    let user_msg = format!(
        "=== SKILL ===\n{}\n\n=== TASK ===\n{}\n\n=== TRANSCRIPT ===\n{}",
        clip(skill_md, 4000),
        task,
        clip_transcript(transcript, 24000),
    );

    let mut last_text = String::new();
    let mut last_err: Option<String> = None;
    for attempt in 0..2 {
        let sys = if attempt == 1 {
            format!(
                "{sys_prompt}\n\nDO NOT use <think> or any reasoning. Your reply \
must START with '{{' and be ONLY the JSON object, nothing else."
            )
        } else {
            sys_prompt.to_string()
        };
        let msgs = vec![
            json!({"role": "system", "content": sys}),
            json!({"role": "user", "content": user_msg}),
        ];
        match crate::src::llm_core::llm_call_async(
            url,
            model,
            msgs,
            0.1,
            32768,
            headers.clone(),
            180,
        )
        .await
        {
            Err(e) => {
                last_err = Some(e.to_string());
                continue;
            }
            Ok(raw) => {
                last_text = raw.clone();
                if let Some(parsed) = parse_verdict(&raw) {
                    return parsed;
                }
            }
        }
    }

    // Key order mirrors Python skills_routes.py:240-243 (preserve_order is on):
    // verdict, confidence, summary, issues, [raw].
    let mut out = Map::new();
    out.insert("verdict".to_string(), Value::String("unknown".to_string()));
    out.insert("confidence".to_string(), json!(0));
    if let Some(e) = last_err.as_ref().filter(|_| last_text.is_empty()) {
        out.insert(
            "summary".to_string(),
            Value::String(format!("Evaluator call failed: {e}")),
        );
        out.insert("issues".to_string(), Value::Array(Vec::new()));
    } else {
        out.insert(
            "summary".to_string(),
            Value::String("Evaluator returned unparseable output.".to_string()),
        );
        out.insert("issues".to_string(), Value::Array(Vec::new()));
        out.insert("raw".to_string(), Value::String(clip(&last_text, 300)));
    }
    out
}

// ===========================================================================
// _eval_skill_necessity (skills_routes.py:246-299)
// ===========================================================================

/// Lenient extraction of the LAST `{ a..b }` span that parses (the simpler
/// find/rfind variant the necessity + retrieval judges use).
fn parse_lenient_object(raw: &str) -> Option<Map<String, Value>> {
    let text = strip_think(raw);
    let (a, b) = (text.find('{')?, text.rfind('}')?);
    if b <= a {
        return None;
    }
    let frag = &text[a..=b];
    for cand in [frag.to_string(), strip_trailing_commas(frag)] {
        if let Ok(Value::Object(m)) = serde_json::from_str::<Value>(&cand) {
            return Some(m);
        }
    }
    None
}

/// `[{"name": ..., "description": ...}, ...]` catalog line builder.
fn catalog_lines(others: &[Map<String, Value>], cap: usize) -> String {
    let lines: Vec<String> = others
        .iter()
        .take(cap)
        .map(|o| {
            format!(
                "- {}: {}",
                str_or_empty(o.get("name")),
                str_or_empty(o.get("description")),
            )
        })
        .collect();
    if lines.is_empty() {
        "(no other skills)".to_string()
    } else {
        lines.join("\n")
    }
}

/// `_eval_skill_necessity(skill_md, others, url, model, headers)` — advisory.
async fn eval_skill_necessity(
    skill_md: &str,
    others: &[Map<String, Value>],
    url: &str,
    model: &str,
    headers: &Headers,
) -> Option<Map<String, Value>> {
    let catalog = catalog_lines(others, usize::MAX);
    let sys_prompt = "You assess whether a reusable AI 'skill' (a saved procedure) is worth keeping. \
A skill is UNNECESSARY if it essentially duplicates another skill in the library, \
OR if it's so trivial/generic that a capable assistant would do it correctly with no \
saved procedure at all. A skill IS necessary if it captures a specific, non-obvious \
procedure, tool sequence, or hard-won detail.\n\n\
Be conservative: only call it unnecessary when you're confident. Reason in \
<think></think> first if needed, then output ONLY this JSON:\n\
{\"necessary\": true|false, \"redundant_with\": [\"skill-name\", ...], \
\"reason\": \"one short sentence\"}";
    let user_msg = format!(
        "=== SKILL UNDER REVIEW ===\n{}\n\n=== OTHER SKILLS IN THE LIBRARY ===\n{}",
        clip(skill_md, 3000),
        clip(&catalog, 4000),
    );
    let raw = match crate::src::llm_core::llm_call_async(
        url,
        model,
        vec![
            json!({"role": "system", "content": sys_prompt}),
            json!({"role": "user", "content": user_msg}),
        ],
        0.1,
        8192,
        headers.clone(),
        120,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => {
            logger::warning(&format!("Necessity check failed: {e}"));
            return None;
        }
    };
    let data = parse_lenient_object(&raw)?;
    if !data.contains_key("necessary") {
        return None;
    }
    let mut out = Map::new();
    // `bool(data.get("necessary", True))`
    out.insert(
        "necessary".to_string(),
        Value::Bool(py_truthy(data.get("necessary").unwrap_or(&Value::Bool(true)))),
    );
    let redundant: Vec<Value> = str_list(data.get("redundant_with"))
        .into_iter()
        .filter(|x| !x.trim().is_empty())
        .map(|x| Value::String(clip(&x, 80)))
        .take(6)
        .collect();
    out.insert("redundant_with".to_string(), Value::Array(redundant));
    out.insert(
        "reason".to_string(),
        Value::String(clip(&str_or_empty(data.get("reason")), 200)),
    );
    Some(out)
}

/// Python truthiness of an arbitrary JSON value (`bool(x)`).
fn py_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}

// ===========================================================================
// _should_check_retrieval_precision (skills_routes.py:302-321, pure)
// ===========================================================================

/// `_should_check_retrieval_precision(skill)` — cheap broad-tag prefilter.
pub(crate) fn should_check_retrieval_precision(skill: &Map<String, Value>) -> bool {
    // The Python `broad` set (literal, dedup-free membership semantics).
    let broad: std::collections::HashSet<&str> = [
        "arch", "arch linux", "linux", "network", "networking", "wifi",
        "installation", "install", "system", "ssh", "document", "documents",
        "search", "email", "calendar", "gpu", "server", "python",
    ]
    .into_iter()
    .collect();
    let tags: std::collections::HashSet<String> = str_list(skill.get("tags"))
        .into_iter()
        .map(|t| t.trim().to_lowercase())
        .collect();
    if tags.iter().any(|t| broad.contains(t.as_str())) {
        return true;
    }
    let text = format!(
        "{} {} {}",
        skill_str(skill, "name"),
        skill_str(skill, "description"),
        skill_str(skill, "when_to_use"),
    )
    .to_lowercase();
    broad.iter().filter(|t| text.contains(**t)).count() >= 2
}

// ===========================================================================
// _eval_skill_retrieval_precision (skills_routes.py:324-385)
// ===========================================================================

/// `_eval_skill_retrieval_precision(skill_md, others, url, model, headers)`.
async fn eval_skill_retrieval_precision(
    skill_md: &str,
    others: &[Map<String, Value>],
    url: &str,
    model: &str,
    headers: &Headers,
) -> Option<Map<String, Value>> {
    let catalog = catalog_lines(others, 80);
    let sys_prompt = "You are auditing retrieval metadata for a reusable AI skill. The app selects \
skills by matching user requests against the skill name, description, tags, \
when_to_use, and procedure text. Judge whether this skill is likely to be \
over-selected for nearby but wrong requests.\n\n\
Focus ONLY on metadata/retrieval precision, not whether the procedure works. \
Flag broad tags such as network, installation, system, document, search, ssh, \
python, gpu, or server when they would cause this narrow skill to match too \
many adjacent tasks. Recommend narrower tags/when_to_use wording. Compare \
against the other skills to spot boundaries.\n\n\
Return ok=true only when the trigger metadata is narrow enough. If not ok, \
issues MUST start with 'metadata: retrieval:' and be actionable. Output ONLY JSON:\n\
{\"ok\": true|false, \"summary\": \"one short sentence\", \"issues\": [\"metadata: retrieval: ...\"]}";
    let user_msg = format!(
        "=== SKILL UNDER REVIEW ===\n{}\n\n=== OTHER SKILLS IN LIBRARY ===\n{}\n\n\
Decide if this skill's retrieval metadata should be narrowed so it only \
fires for its intended scenario and not for adjacent skills above.",
        clip(skill_md, 5000),
        clip(&catalog, 5000),
    );
    let raw = match crate::src::llm_core::llm_call_async(
        url,
        model,
        vec![
            json!({"role": "system", "content": sys_prompt}),
            json!({"role": "user", "content": user_msg}),
        ],
        0.1,
        4096,
        headers.clone(),
        90,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => {
            logger::warning(&format!("Retrieval precision check failed: {e}"));
            return None;
        }
    };
    let data = parse_lenient_object(&raw)?;
    if !data.contains_key("ok") {
        return None;
    }
    let mut out = Map::new();
    out.insert(
        "ok".to_string(),
        Value::Bool(py_truthy(data.get("ok").unwrap_or(&Value::Null))),
    );
    out.insert(
        "summary".to_string(),
        Value::String(clip(&str_or_empty(data.get("summary")), 300)),
    );
    let issues: Vec<Value> = str_list(data.get("issues"))
        .into_iter()
        .filter(|x| !x.trim().is_empty())
        .map(|x| Value::String(clip(&x, 220)))
        .take(6)
        .collect();
    out.insert("issues".to_string(), Value::Array(issues));
    Some(out)
}

// ===========================================================================
// The stream_agent_loop collect-loop helpers
// ===========================================================================

/// `d.get("delta")` truthy non-empty string.
fn delta_str(d: &Value) -> Option<&str> {
    match d.get("delta") {
        Some(Value::String(s)) if !s.is_empty() => Some(s),
        _ => None,
    }
}

/// `d.get("type")`.
fn type_of(d: &Value) -> &str {
    d.get("type").and_then(|v| v.as_str()).unwrap_or("")
}

/// `str(d.get("command") or d.get("args") or "")[:300]`.
fn tool_command(d: &Value) -> String {
    let cmd = match d.get("command") {
        Some(v) if py_truthy(v) => str_or_empty(Some(v)),
        _ => match d.get("args") {
            Some(v) if py_truthy(v) => str_or_empty(Some(v)),
            _ => String::new(),
        },
    };
    clip(&cmd, 300)
}

/// `f"--- round {d.get('round')} ---"` — Python str() of the round (None-safe).
fn round_str(round: &Value) -> String {
    match round {
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

// ===========================================================================
// _run_skill_test_job (skills_routes.py:394-475)
// ===========================================================================

/// `_run_skill_test_job(...)` — run the skill in an agent loop, capture a
/// condensed log + transcript, judge, set_audit, confidence-nudge, status done.
#[allow(clippy::too_many_arguments)]
pub(crate) async fn run_skill_test_job(
    key: &str,
    name: &str,
    md: &str,
    task: &str,
    url: &str,
    model: &str,
    headers: Headers,
    owner: Option<String>,
    sm: &SkillsManager,
) {
    use crate::src::agent_loop::{stream_agent_loop, AgentLoopArgs};
    use futures_util::StreamExt;

    // `job = _skill_test_jobs.get(key); if job is None: return`
    if get_test_job(key).is_none() {
        return;
    }

    let mut transcript: Vec<String> = Vec::new();
    let mut say_buf: Vec<String> = Vec::new();

    let messages = vec![
        json!({"role": "system", "content": format!(
            "You are TESTING a skill. Below is a reusable skill (a procedure). Follow it \
to complete the user's task for real, using your available tools, step by \
step. If the skill is wrong, unclear, or references tools that don't exist, \
do your best — the problems will be reviewed afterward.\n\n=== SKILL ===\n{md}"
        )}),
        json!({"role": "user", "content": task}),
    ];

    let args = AgentLoopArgs {
        endpoint_url: url.to_string(),
        model: model.to_string(),
        messages,
        headers: headers.clone(),
        temperature: 0.3,
        max_tokens: 0,
        max_rounds: 8,
        owner: owner.clone(),
        ..Default::default()
    };

    // Append one log entry + apply the 600-cap (skills_routes.py:447-448).
    let push_log = |entry: Value| {
        with_test_job(key, |job| {
            if let Some(Value::Array(log)) = job.get_mut("log") {
                log.push(entry);
                if log.len() > 600 {
                    let drop = log.len() - 600;
                    log.drain(0..drop);
                }
            }
        });
    };
    // `_flush_say()` — flush the say buffer into a single say entry.
    let flush_say = |say_buf: &mut Vec<String>| {
        if !say_buf.is_empty() {
            let text = say_buf.concat();
            say_buf.clear();
            push_log(json!({"type": "say", "text": text}));
        }
    };

    // The collect-loop. The Rust stream yields no error item, so the Python
    // `try/except Exception as e:` arm has no source here (documented). The
    // structure (flush_say + evaluating + judge) is preserved faithfully.
    let inner = stream_agent_loop(args);
    futures_util::pin_mut!(inner);
    while let Some(chunk) = inner.next().await {
        // `if not chunk.startswith("data: ") or chunk.strip() == "data: [DONE]": continue`
        let rest = match chunk.strip_prefix("data: ") {
            Some(r) => r,
            None => continue,
        };
        if chunk.trim() == "data: [DONE]" {
            continue;
        }
        let d: Value = match serde_json::from_str(rest) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(delta) = delta_str(&d) {
            say_buf.push(delta.to_string());
            transcript.push(delta.to_string());
        } else {
            match type_of(&d) {
                "tool_start" => {
                    flush_say(&mut say_buf);
                    let cmd = tool_command(&d);
                    let tool = d.get("tool").cloned().unwrap_or(Value::Null);
                    push_log(json!({"type": "tool_start", "tool": tool, "command": cmd}));
                    transcript.push(format!(
                        "\n[tool {}] {cmd}\n",
                        d.get("tool").and_then(|v| v.as_str()).unwrap_or("None")
                    ));
                }
                "tool_output" => {
                    flush_say(&mut say_buf);
                    let out = clip(&str_or_empty(d.get("output")), 600);
                    push_log(json!({"type": "tool_output", "output": out}));
                    transcript.push(format!("[output] {out}\n"));
                }
                "agent_step" => {
                    flush_say(&mut say_buf);
                    let round = d.get("round").cloned().unwrap_or(Value::Null);
                    push_log(json!({"type": "agent_step", "round": round.clone()}));
                    transcript.push(format!("\n--- round {} ---\n", round_str(&round)));
                }
                _ => {}
            }
        }
    }
    flush_say(&mut say_buf);

    push_log(json!({"type": "evaluating"}));
    let verdict = eval_skill_run(md, task, &transcript.concat(), url, model, &headers).await;
    with_test_job(key, |job| {
        job.insert("verdict".to_string(), Value::Object(verdict.clone()));
    });

    // Confidence nudge (skills_routes.py:462-475). A manual test never involves
    // the teacher -> teacher_model is "".
    let v = verdict
        .get("verdict")
        .and_then(|x| x.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("unknown")
        .to_string();
    // best-effort: `skills_manager.set_audit(name, v, by_teacher=False, worker_model=model, owner=owner)`
    sm.set_audit(name, &v, false, model, "", owner.as_deref());
    let conf = match v.as_str() {
        "pass" => Some(0.95),
        "needs_work" => Some(0.6),
        "fail" => Some(0.4),
        _ => None,
    };
    if let Some(c) = conf {
        let mut up = Map::new();
        up.insert("confidence".to_string(), json!(c));
        // `skills_manager.update_skill(name, {"confidence": conf}, owner=owner)`
        sm.update_skill(name, &up, owner.as_deref());
    }
    with_test_job(key, |job| {
        job.insert("status".to_string(), Value::String("done".to_string()));
    });
}

// ===========================================================================
// _run_skill_test_once (skills_routes.py:671-703)
// ===========================================================================

/// `_run_skill_test_once(md, task, url, model, headers, owner)` -> (transcript, verdict).
/// Same collect-loop as the job but transcript-only (no log, no 600-cap).
pub(crate) async fn run_skill_test_once(
    md: &str,
    task: &str,
    url: &str,
    model: &str,
    headers: Headers,
    owner: Option<String>,
) -> (String, Map<String, Value>) {
    use crate::src::agent_loop::{stream_agent_loop, AgentLoopArgs};
    use futures_util::StreamExt;

    let mut transcript: Vec<String> = Vec::new();
    let messages = vec![
        json!({"role": "system", "content": format!(
            "You are TESTING a skill. Follow this skill's procedure to complete the task \
for real, using your tools, step by step.\n\n=== SKILL ===\n{md}"
        )}),
        json!({"role": "user", "content": task}),
    ];
    let args = AgentLoopArgs {
        endpoint_url: url.to_string(),
        model: model.to_string(),
        messages,
        headers: headers.clone(),
        temperature: 0.3,
        max_tokens: 0,
        max_rounds: 8,
        owner: owner.clone(),
        ..Default::default()
    };

    let inner = stream_agent_loop(args);
    futures_util::pin_mut!(inner);
    while let Some(chunk) = inner.next().await {
        let rest = match chunk.strip_prefix("data: ") {
            Some(r) => r,
            None => continue,
        };
        if chunk.trim() == "data: [DONE]" {
            continue;
        }
        let d: Value = match serde_json::from_str(rest) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(delta) = delta_str(&d) {
            transcript.push(delta.to_string());
        } else {
            match type_of(&d) {
                "tool_start" => transcript.push(format!(
                    "\n[tool {}] {}\n",
                    d.get("tool").and_then(|v| v.as_str()).unwrap_or("None"),
                    tool_command(&d),
                )),
                "tool_output" => transcript.push(format!(
                    "[output] {}\n",
                    clip(&str_or_empty(d.get("output")), 600)
                )),
                "agent_step" => transcript.push(format!(
                    "\n--- round {} ---\n",
                    round_str(d.get("round").unwrap_or(&Value::Null))
                )),
                _ => {}
            }
        }
    }
    let text = transcript.concat();
    let verdict = eval_skill_run(md, task, &text, url, model, &headers).await;
    (text, verdict)
}

// ===========================================================================
// _improve_skill_md (skills_routes.py:706-750)
// ===========================================================================

/// `_improve_skill_md(skill_md, verdict, transcript, url, model, headers)` — have
/// a model rewrite SKILL.md to fix the reviewer's issues. Returns the corrected
/// markdown or None.
pub(crate) async fn improve_skill_md(
    skill_md: &str,
    verdict: &Map<String, Value>,
    transcript: &str,
    url: &str,
    model: &str,
    headers: &Headers,
) -> Option<String> {
    // `issues = "\n".join("- " + str(i) for i in (verdict.get("issues") or []))`
    let issues = str_list(verdict.get("issues"))
        .iter()
        .map(|i| format!("- {i}"))
        .collect::<Vec<_>>()
        .join("\n");
    let sys_prompt = "You are improving a reusable AI SKILL written in Markdown (frontmatter + body). \
A QA reviewer found problems after a test run. Rewrite the SKILL.md to fix them: \
make vague steps concrete, correct or remove references to tools that don't exist, \
ensure the procedure is reproducible. \
Keep the `name` field EXACTLY as-is (it is the skill's identity / filename). You MAY \
correct the OTHER frontmatter — tags, category, when_to_use, description — when the \
reviewer flagged them (issues prefixed 'metadata:') or they don't match the body; keep \
retrieval metadata narrow: remove broad tags that would over-select the skill, and make \
`when_to_use` say when NOT to use the skill if adjacent tasks are easy to confuse. Keep \
valid frontmatter structure. Do NOT invent capabilities the agent lacks. Reason in \
<think></think> first if needed, then output ONLY the full corrected SKILL.md (no \
fences, no commentary).";
    let user_msg = format!(
        "=== CURRENT SKILL.md ===\n{skill_md}\n\n=== REVIEWER VERDICT ===\n{}\nIssues:\n{issues}\n\n\
=== TEST TRANSCRIPT ===\n{}",
        str_or_empty(verdict.get("summary")),
        clip(transcript, 6000),
    );
    let raw = match crate::src::llm_core::llm_call_async(
        url,
        model,
        vec![
            json!({"role": "system", "content": sys_prompt}),
            json!({"role": "user", "content": user_msg}),
        ],
        0.2,
        16384,
        headers.clone(),
        180,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => {
            logger::warning(&format!("Audit: improve call failed: {e}"));
            return None;
        }
    };
    // Strip <think>...</think>, <think>...$, lone </think>; then trim.
    let t = THINK_CLOSED_RE.replace_all(&raw, "");
    let t = THINK_OPEN_RE.replace_all(&t, "");
    let t = THINK_LONE_CLOSE_RE.replace_all(&t, "");
    let mut text = t.trim().to_string();
    // Strip a leading ``` fence: `text.split("\n", 1)[-1].rsplit("```", 1)[0]`.
    if text.starts_with("```") {
        let after_first_line = match text.split_once('\n') {
            Some((_, rest)) => rest.to_string(),
            None => text.clone(),
        };
        let before_last_fence = match after_first_line.rfind("```") {
            Some(idx) => after_first_line[..idx].to_string(),
            None => after_first_line,
        };
        text = before_last_fence.trim().to_string();
    }
    // Keep ONLY the LAST complete-looking frontmatter doc: the last `^---\n`
    // doc-start that has a `^name:` line somewhere ahead of it (the Python
    // lookahead, reconstructed without lookahead support).
    let last_start = FRONTMATTER_START_RE
        .find_iter(&text)
        .filter(|m| has_name_line_ahead(&text, m.start()))
        .map(|m| m.start())
        .last();
    if let Some(start) = last_start {
        text = text[start..].trim().to_string();
    }
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

// ===========================================================================
// _audit_auto_publish_policy (skills_routes.py:482-499)
// ===========================================================================

/// `_audit_auto_publish_policy(owner)` -> (auto_publish_enabled, min_confidence).
fn audit_auto_publish_policy(owner: Option<&str>) -> (bool, f64) {
    let prefs = crate::routes::prefs_store::load_for_user(owner);
    let prefs = prefs.as_object();
    // `default_min = get_setting("skill_autosave_min_confidence", 0.85)`
    let default_min = crate::src::settings::get_setting("skill_autosave_min_confidence", json!(0.85))
        .as_f64()
        .unwrap_or(0.85);
    // `enabled = bool(prefs.get("auto_approve_skills", True))`
    let enabled = match prefs.and_then(|p| p.get("auto_approve_skills")) {
        Some(v) => py_truthy(v),
        None => true,
    };
    // `min_conf = float(prefs.get("skill_min_confidence", default_min))`
    let min_conf = match prefs.and_then(|p| p.get("skill_min_confidence")) {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(default_min),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(0.85),
        // Python `float(x)` on a non-numeric raises -> the except sets 0.85.
        Some(_) => 0.85,
        None => default_min,
    };
    (enabled, min_conf.clamp(0.0, 1.0))
}

// ===========================================================================
// _skill_duplicate_blocker (skills_routes.py:502-570)
// ===========================================================================

static DUP_NUM_TAG_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"-\d+\b").unwrap());
static DUP_SPLIT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z0-9]+").unwrap());
static DUP_BASE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"-\d+$").unwrap());

/// `_tokens(sk)` (skills_routes.py:511-523).
fn dup_tokens(sk: &Map<String, Value>) -> std::collections::HashSet<String> {
    let text = format!(
        "{} {} {} {} {}",
        skill_str(sk, "name"),
        skill_str(sk, "description"),
        skill_str(sk, "when_to_use"),
        str_list(sk.get("procedure")).join(" "),
        str_list(sk.get("tags")).join(" "),
    )
    .to_lowercase();
    let text = DUP_NUM_TAG_RE.replace_all(&text, "");
    let stop: std::collections::HashSet<&str> =
        ["the", "and", "with", "for", "from", "using"].into_iter().collect();
    DUP_SPLIT_RE
        .split(&text)
        .filter(|t| t.chars().count() > 2 && !stop.contains(t))
        .map(|t| t.to_string())
        .collect()
}

/// `_sim(a, b)` — token Jaccard.
fn dup_sim(a: &Map<String, Value>, b: &Map<String, Value>) -> f64 {
    let (ta, tb) = (dup_tokens(a), dup_tokens(b));
    if ta.is_empty() || tb.is_empty() {
        return 0.0;
    }
    let inter = ta.intersection(&tb).count();
    let union = ta.union(&tb).count().max(1);
    inter as f64 / union as f64
}

/// `_base(n)` — strip a trailing `-<num>` suffix.
fn dup_base(n: &str) -> String {
    DUP_BASE_RE.replace(n, "").to_string()
}

/// `_score(sk)` — keeper ranking (skills_routes.py:534-541).
fn dup_score(sk: &Map<String, Value>) -> f64 {
    let published = if skill_str(sk, "status") == "published" {
        100000.0
    } else {
        0.0
    };
    let uses = sk.get("uses").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let confidence = sk.get("confidence").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let by_teacher = if py_truthy(sk.get("audit_by_teacher").unwrap_or(&Value::Null)) {
        -5.0
    } else {
        0.0
    };
    let name_len = skill_str(sk, "name").chars().count() as f64;
    published + (uses * 100.0) + (confidence * 100.0).round() + by_teacher - (name_len / 1000.0)
}

/// `_skill_duplicate_blocker(skills_manager, name, owner)` -> keeper name when
/// `name` is a lower-priority duplicate, else None.
fn skill_duplicate_blocker(sm: &SkillsManager, name: &str, owner: Option<&str>) -> Option<String> {
    let skills = sm.load(owner);
    // `next((s for s in skills if (s.get("name") or s.get("id")) == name), None)`
    let current = skills.iter().find(|s| {
        let n = skill_str(s, "name");
        let id = skill_str(s, "id");
        (if n.is_empty() { id } else { n }) == name
    })?;
    let cur_name = {
        let n = skill_str(current, "name");
        if !n.is_empty() {
            n
        } else {
            let id = skill_str(current, "id");
            if !id.is_empty() {
                id
            } else {
                name.to_string()
            }
        }
    };
    let mut duplicates: Vec<&Map<String, Value>> = Vec::new();
    for other in &skills {
        let other_name = {
            let n = skill_str(other, "name");
            if n.is_empty() {
                skill_str(other, "id")
            } else {
                n
            }
        };
        if other_name.is_empty() || other_name == cur_name {
            continue;
        }
        if dup_base(&cur_name) == dup_base(&other_name) || dup_sim(current, other) >= 0.38 {
            duplicates.push(other);
        }
    }
    if duplicates.is_empty() {
        return None;
    }
    // `keeper = sorted([current, *duplicates], key=_score, reverse=True)[0]`
    let mut pool: Vec<&Map<String, Value>> = vec![current];
    pool.extend(duplicates);
    pool.sort_by(|a, b| {
        dup_score(b)
            .partial_cmp(&dup_score(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let keeper = pool[0];
    let keeper_name = {
        let n = skill_str(keeper, "name");
        if !n.is_empty() {
            n
        } else {
            skill_str(keeper, "id")
        }
    };
    if !keeper_name.is_empty() && keeper_name != cur_name {
        // `set_necessity(cur_name, False, [keeper_name], ..., owner=owner)`
        sm.set_necessity(
            &cur_name,
            false,
            Some(std::slice::from_ref(&keeper_name)),
            &format!("Lower-priority duplicate of {keeper_name}"),
            owner,
        );
        return Some(keeper_name);
    }
    None
}

// ===========================================================================
// _audit_flag_text + _audit_generic_blocker (skills_routes.py:573-610)
// ===========================================================================

static GENERIC_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(too[-\s]?generic|generic|trivial|capable assistant|without a saved|not need|unnecessary|irrelevant)\b",
    )
    .unwrap()
});

/// `_audit_flag_text(*parts)` — flatten dicts/lists/scalars into one lowercase string.
fn audit_flag_text(parts: &[&Value]) -> String {
    let mut out: Vec<String> = Vec::new();
    for part in parts {
        match part {
            Value::Object(m) => out.extend(m.values().map(|v| str_or_empty(Some(v)))),
            Value::Array(a) => out.extend(a.iter().map(|v| str_or_empty(Some(v)))),
            other => out.push(str_or_empty(Some(other))),
        }
    }
    out.join(" ").to_lowercase()
}

/// `_audit_generic_blocker(skill, necessity, verdict_data)` -> reason or None.
fn audit_generic_blocker(
    skill: Option<&Map<String, Value>>,
    necessity: Option<&Map<String, Value>>,
    verdict_data: Option<&Map<String, Value>>,
) -> Option<String> {
    if let Some(nec) = necessity {
        let reason = str_or_empty(nec.get("reason"));
        let necessary_false = matches!(nec.get("necessary"), Some(Value::Bool(false)));
        if necessary_false && GENERIC_RE.is_match(&reason) {
            return Some(if reason.is_empty() {
                "Generic or unnecessary skill".to_string()
            } else {
                reason
            });
        }
    }
    if let Some(sk) = skill {
        let tags = sk.get("tags").cloned().unwrap_or(Value::Array(Vec::new()));
        let tag_text = audit_flag_text(&[&tags]);
        if GENERIC_RE.is_match(&tag_text) {
            return Some("Skill is tagged generic".to_string());
        }
    }
    if let Some(vd) = verdict_data {
        let summary = vd.get("summary").cloned().unwrap_or(Value::Null);
        let issues = vd.get("issues").cloned().unwrap_or(Value::Array(Vec::new()));
        let verdict_text = audit_flag_text(&[&summary, &issues]);
        if GENERIC_RE.is_match(&verdict_text) {
            return Some("Audit flagged the skill as generic or unnecessary".to_string());
        }
    }
    None
}

// ===========================================================================
// _audit_finalize_status (skills_routes.py:613-644)
// ===========================================================================

/// `_audit_finalize_status(...)` — apply the publishing policy, returning the
/// new status ("published" | "draft").
fn audit_finalize_status(
    sm: &SkillsManager,
    name: &str,
    owner: Option<&str>,
    verdict: &str,
    confidence: f64,
    necessity: Option<&Map<String, Value>>,
    verdict_data: Option<&Map<String, Value>>,
) -> String {
    let (auto_publish, min_conf) = audit_auto_publish_policy(owner);
    let mut necessary = true;
    let loaded = sm.load(owner);
    let current = find_by_name(&loaded, name);
    let generic_reason = audit_generic_blocker(current, necessity, verdict_data);
    if matches!(
        necessity.and_then(|n| n.get("necessary")),
        Some(Value::Bool(false))
    ) {
        necessary = false;
    }
    if let Some(reason) = generic_reason {
        necessary = false;
        // `skills_manager.set_necessity(name, False, [], generic_reason, owner=owner)`
        sm.set_necessity(name, false, Some(&[]), &reason, owner);
    }
    let duplicate_of = if verdict == "pass" {
        skill_duplicate_blocker(sm, name, owner)
    } else {
        None
    };
    if duplicate_of.is_some() {
        necessary = false;
    }
    let status = if auto_publish && necessary && verdict == "pass" && confidence >= min_conf {
        "published"
    } else {
        "draft"
    };
    let mut up = Map::new();
    up.insert("status".to_string(), Value::String(status.to_string()));
    // `skills_manager.update_skill(name, {"status": status}, owner=owner)`
    sm.update_skill(name, &up, owner);
    status.to_string()
}

// ===========================================================================
// _apply_skill_md (skills_routes.py:647-668)
// ===========================================================================

/// `_apply_skill_md(skills_manager, name, md, owner)` — parse + persist an edited
/// SKILL.md, PINNING the name. Returns true on success.
fn apply_skill_md(sm: &SkillsManager, name: &str, md: &str, owner: Option<&str>) -> bool {
    use crate::services::memory::skill_format::Skill;
    let mut sk = Skill::from_markdown(md, None);
    // Pin the identity — the fixer must NEVER rename the skill.
    sk.name = name.to_string();
    let owner_val = match sk.owner.filter(|o| !o.is_empty()) {
        Some(o) => Value::String(o),
        None => match owner {
            Some(o) => Value::String(o.to_string()),
            None => Value::Null,
        },
    };
    let mut up = Map::new();
    up.insert("name".to_string(), Value::String(sk.name.clone()));
    up.insert("description".to_string(), Value::String(sk.description));
    up.insert("version".to_string(), Value::String(sk.version));
    up.insert("category".to_string(), Value::String(sk.category));
    up.insert("tags".to_string(), json!(sk.tags));
    up.insert("platforms".to_string(), json!(sk.platforms));
    up.insert("requires_toolsets".to_string(), json!(sk.requires_toolsets));
    up.insert("fallback_for_toolsets".to_string(), json!(sk.fallback_for_toolsets));
    up.insert("status".to_string(), Value::String(sk.status));
    up.insert("confidence".to_string(), json!(sk.confidence));
    up.insert("source".to_string(), Value::String(sk.source));
    up.insert(
        "teacher_model".to_string(),
        match sk.teacher_model {
            Some(t) => Value::String(t),
            None => Value::Null,
        },
    );
    up.insert("owner".to_string(), owner_val);
    up.insert("when_to_use".to_string(), Value::String(sk.when_to_use));
    up.insert("procedure".to_string(), json!(sk.procedure));
    up.insert("pitfalls".to_string(), json!(sk.pitfalls));
    up.insert("verification".to_string(), json!(sk.verification));
    up.insert("body_extra".to_string(), Value::String(sk.body_extra));
    // `skills_manager.update_skill(name, {...}, owner=owner)`
    sm.update_skill(name, &up, owner)
}

// ===========================================================================
// _audit_one_skill (skills_routes.py:753-913)
// ===========================================================================

/// `(refreshed or {}).get("necessity")` — the necessity sidecar dict if present.
fn necessity_of(s: &Map<String, Value>) -> Option<Map<String, Value>> {
    match s.get("necessity") {
        Some(Value::Object(m)) => Some(m.clone()),
        _ => None,
    }
}

/// `_audit_one_skill(...)` — test → judge → self-edit → retry → teacher → flag.
/// `log` records progress (the closure appends to the audit job's log).
#[allow(clippy::too_many_arguments)]
pub(crate) async fn audit_one_skill(
    sm: &SkillsManager,
    skill: &Map<String, Value>,
    url: &str,
    model: &str,
    headers: &Headers,
    teacher: Option<&Teacher>,
    owner: Option<&str>,
    log: &mut (dyn FnMut(String) + Send),
) -> Map<String, Value> {
    let name = skill_name(skill);
    // `_set_conf(c)`
    let set_conf = |c: f64| {
        let mut up = Map::new();
        up.insert("confidence".to_string(), json!(c));
        // `skills_manager.update_skill(name, {"confidence": c}, owner=owner)`
        sm.update_skill(&name, &up, owner);
    };

    // `md = skills_manager.read_skill_md(name, owner=owner)`
    let md = sm.read_skill_md(&name, owner).unwrap_or_default();
    if md.is_empty() {
        log(format!("{name}: no source — skipped"));
        return json!({"skill": name, "result": "skipped"})
            .as_object()
            .unwrap()
            .clone();
    }
    let mut md = md;
    let mut skill = skill.clone();

    // Advisory necessity check over SAME-owner others.
    let sk_owner = skill_str(&skill, "owner");
    let others: Vec<Map<String, Value>> = sm
        .load(owner)
        .into_iter()
        .filter(|s| {
            let n = skill_name(s);
            if n.is_empty() || n == name {
                return false;
            }
            // `(not sk_owner or not s.get("owner") or s.get("owner") == sk_owner)`
            let s_owner = skill_str(s, "owner");
            sk_owner.is_empty() || s_owner.is_empty() || s_owner == sk_owner
        })
        .map(|s| {
            let mut m = Map::new();
            m.insert("name".to_string(), Value::String(skill_name(&s)));
            m.insert(
                "description".to_string(),
                Value::String(skill_str(&s, "description")),
            );
            m
        })
        .collect();
    let nec = eval_skill_necessity(&md, &others, url, model, headers).await;
    if let Some(ref n) = nec {
        let redundant = str_list(n.get("redundant_with"));
        // `set_necessity(name, nec.necessary, nec.redundant_with, nec.reason, owner=owner)`
        sm.set_necessity(
            &name,
            py_truthy(n.get("necessary").unwrap_or(&Value::Bool(true))),
            Some(&redundant),
            &str_or_empty(n.get("reason")),
            owner,
        );
        if !py_truthy(n.get("necessary").unwrap_or(&Value::Bool(true))) {
            log(format!(
                "{name}: possibly unnecessary — {}",
                clip(&str_or_empty(n.get("reason")), 80)
            ));
        }
    }

    let generic_reason = audit_generic_blocker(Some(&skill), nec.as_ref(), None);
    let duplicate_of = skill_duplicate_blocker(sm, &name, owner);
    let nec_false = matches!(
        nec.as_ref().and_then(|n| n.get("necessary")),
        Some(Value::Bool(false))
    );
    if generic_reason.is_some() || duplicate_of.is_some() || nec_false {
        let reason = generic_reason.clone().unwrap_or_else(|| {
            if let Some(d) = &duplicate_of {
                format!("Lower-priority duplicate of {d}")
            } else {
                let r = nec
                    .as_ref()
                    .map(|n| str_or_empty(n.get("reason")))
                    .unwrap_or_default();
                if r.is_empty() {
                    "Unnecessary skill".to_string()
                } else {
                    r
                }
            }
        });
        let mut up = Map::new();
        up.insert("status".to_string(), Value::String("draft".to_string()));
        up.insert("confidence".to_string(), json!(0.35));
        // `update_skill(name, {"status": "draft", "confidence": 0.35}, owner=owner)`
        sm.update_skill(&name, &up, owner);
        // `set_audit(name, "skipped", by_teacher=False, worker_model=model, owner=owner)`
        sm.set_audit(&name, "skipped", false, model, "", owner);
        if let Some(d) = &duplicate_of {
            // `set_necessity(name, False, [duplicate_of], reason, owner=owner)`
            sm.set_necessity(&name, false, Some(std::slice::from_ref(d)), &reason, owner);
        } else {
            // `set_necessity(name, False, [], reason, owner=owner)`
            sm.set_necessity(&name, false, Some(&[]), &reason, owner);
        }
        log(format!(
            "{name}: draft — skipped functional test ({})",
            clip(&reason, 100)
        ));
        return json!({
            "skill": name, "result": "skipped", "reason": reason,
            "confidence": 0.35, "status": "draft"
        })
        .as_object()
        .unwrap()
        .clone();
    }

    // Retrieval precision check (metadata-only fix before the functional test).
    if should_check_retrieval_precision(&skill) {
        if let Some(rp) = eval_skill_retrieval_precision(&md, &others, url, model, headers).await {
            if !py_truthy(rp.get("ok").unwrap_or(&Value::Null)) {
                let mut issues = str_list(rp.get("issues"));
                if issues.is_empty() {
                    issues.push(
                        "metadata: retrieval: narrow tags and when_to_use to the intended trigger"
                            .to_string(),
                    );
                }
                let summary = str_or_empty(rp.get("summary"));
                let head = if summary.is_empty() {
                    issues[0].clone()
                } else {
                    summary.clone()
                };
                log(format!(
                    "{name}: narrowing retrieval metadata — {}",
                    clip(&head, 80)
                ));
                let mut rp_verdict = Map::new();
                rp_verdict.insert("verdict".to_string(), Value::String("pass".to_string()));
                rp_verdict.insert("confidence".to_string(), json!(1.0));
                rp_verdict.insert(
                    "summary".to_string(),
                    Value::String(if summary.is_empty() {
                        "Retrieval metadata is too broad.".to_string()
                    } else {
                        summary
                    }),
                );
                rp_verdict.insert(
                    "issues".to_string(),
                    Value::Array(issues.into_iter().map(Value::String).collect()),
                );
                let fixed = improve_skill_md(
                    &md,
                    &rp_verdict,
                    "Retrieval audit only: the procedure may work, but matching metadata is too broad.",
                    url,
                    model,
                    headers,
                )
                .await;
                if let Some(fixed) = fixed {
                    if fixed.trim() != md.trim() && apply_skill_md(sm, &name, &fixed, owner) {
                        md = fixed;
                        if let Some(refreshed) =
                            sm.load(owner).into_iter().find(|s| skill_name(s) == name)
                        {
                            skill = refreshed;
                        }
                    }
                }
            }
        }
    }

    let task = skill_test_task(&skill);
    log(format!("{name}: testing…"));
    let (mut transcript, mut verdict) =
        run_skill_test_once(&md, &task, url, model, headers.clone(), owner.map(|s| s.to_string())).await;
    let mut v = str_or_empty(verdict.get("verdict"));
    // Python `f"{verdict.get('verdict')}"` (skills_routes.py:838-839): plain
    // str() of the raw value, so a missing key renders the literal "None".
    log(format!(
        "{name}: verdict = {} ({})",
        verdict.get("verdict").map_or_else(|| "None".to_string(), plain_str),
        clip(&str_or_empty(verdict.get("summary")), 80)
    ));

    if v == "pass" {
        // Metadata-only one-shot fixer (can't break a passing run; no re-test).
        let meta_issues: Vec<String> = str_list(verdict.get("issues"))
            .into_iter()
            .filter(|i| i.to_lowercase().trim_start().starts_with("metadata:"))
            .collect();
        if !meta_issues.is_empty() {
            log(format!(
                "{name}: pass, but fixing {} metadata issue(s)…",
                meta_issues.len()
            ));
            if let Some(fixed) =
                improve_skill_md(&md, &verdict, &transcript, url, model, headers).await
            {
                if fixed.trim() != md.trim() {
                    apply_skill_md(sm, &name, &fixed, owner);
                }
            }
        }
        set_conf(0.95);
        // `set_audit(name, "pass", by_teacher=False, worker_model=model, owner=owner)`
        sm.set_audit(&name, "pass", false, model, "", owner);
        let loaded = sm.load(owner);
        let nec_ref = find_by_name(&loaded, &name).and_then(necessity_of);
        let status =
            audit_finalize_status(sm, &name, owner, "pass", 0.95, nec_ref.as_ref(), Some(&verdict));
        log(format!("{name}: {status} — confidence 95%"));
        return json!({
            "skill": name, "result": "pass", "verdict": verdict,
            "confidence": 0.95, "status": status
        })
        .as_object()
        .unwrap()
        .clone();
    }

    if v == "unknown" || v == "inconclusive" {
        // `set_audit(name, "inconclusive", by_teacher=False, worker_model=model, owner=owner)`
        sm.set_audit(&name, "inconclusive", false, model, "", owner);
        let conf = skill.get("confidence").and_then(|c| c.as_f64()).unwrap_or(0.0);
        let nec_ref = necessity_of(&skill);
        let status =
            audit_finalize_status(sm, &name, owner, "inconclusive", conf, nec_ref.as_ref(), None);
        log(format!("{name}: {status} — inconclusive"));
        return json!({
            "skill": name, "result": "inconclusive", "verdict": verdict, "status": status
        })
        .as_object()
        .unwrap()
        .clone();
    }

    // Self-edit + retry.
    log(format!("{name}: self-editing to fix issues…"));
    if let Some(new_md) = improve_skill_md(&md, &verdict, &transcript, url, model, headers).await {
        if new_md.trim() != md.trim() && apply_skill_md(sm, &name, &new_md, owner) {
            md = new_md;
            let (t2, v2) = run_skill_test_once(&md, &task, url, model, headers.clone(), owner.map(|s| s.to_string())).await;
            transcript = t2;
            verdict = v2;
            v = str_or_empty(verdict.get("verdict"));
            log(format!("{name}: retry (self) = {v}"));
            if v == "pass" {
                set_conf(0.85);
                // `set_audit(name, "pass", by_teacher=False, worker_model=model, owner=owner)`
                sm.set_audit(&name, "pass", false, model, "", owner);
                let loaded = sm.load(owner);
                let nec_ref = find_by_name(&loaded, &name).and_then(necessity_of);
                let status = audit_finalize_status(
                    sm, &name, owner, "pass", 0.85, nec_ref.as_ref(), Some(&verdict),
                );
                log(format!("{name}: {status} — confidence 85% after self-edit"));
                return json!({
                    "skill": name, "result": "pass_after_self_edit", "verdict": verdict,
                    "confidence": 0.85, "status": status
                })
                .as_object()
                .unwrap()
                .clone();
            }
        }
    }

    // Teacher escalation (only when a DISTINCT teacher model is configured).
    let mut teacher_ran = false;
    if let Some((t_url, t_model, t_headers)) = teacher {
        if !t_url.is_empty() && !t_model.is_empty() && (t_model != model || t_url != url) {
            teacher_ran = true;
            log(format!("{name}: teacher {t_model} rewriting the skill…"));
            if let Some(t_md) =
                improve_skill_md(&md, &verdict, &transcript, t_url, t_model, t_headers).await
            {
                if t_md.trim() != md.trim() && apply_skill_md(sm, &name, &t_md, owner) {
                    md = t_md;
                }
            }
            // Re-test with the STUDENT model.
            let (_t3, v3) = run_skill_test_once(&md, &task, url, model, headers.clone(), owner.map(|s| s.to_string())).await;
            verdict = v3;
            v = str_or_empty(verdict.get("verdict"));
            log(format!("{name}: retry on student after teacher rewrite = {v}"));
            if v == "pass" {
                set_conf(0.8);
                // `set_audit(name, "pass", by_teacher=True, worker_model=model, teacher_model=t_model, owner=owner)`
                sm.set_audit(&name, "pass", true, model, t_model, owner);
                let loaded = sm.load(owner);
                let nec_ref = find_by_name(&loaded, &name).and_then(necessity_of);
                let status = audit_finalize_status(
                    sm, &name, owner, "pass", 0.8, nec_ref.as_ref(), Some(&verdict),
                );
                log(format!("{name}: {status} — confidence 80% after teacher rewrite"));
                return json!({
                    "skill": name, "result": "pass_after_teacher", "verdict": verdict,
                    "confidence": 0.8, "status": status
                })
                .as_object()
                .unwrap()
                .clone();
            }
        }
    }

    // Still failing -> demote to draft + low confidence + flag (never delete).
    let mut up = Map::new();
    up.insert("status".to_string(), Value::String("draft".to_string()));
    up.insert("confidence".to_string(), json!(0.35));
    // `update_skill(name, {"status": "draft", "confidence": 0.35}, owner=owner)`
    sm.update_skill(&name, &up, owner);
    let final_verdict = if v.is_empty() { "fail".to_string() } else { v };
    let teacher_model = if teacher_ran {
        teacher.map(|t| t.1.as_str()).unwrap_or("")
    } else {
        ""
    };
    // `set_audit(name, v or "fail", by_teacher=teacher_ran, worker_model=model,
    //            teacher_model=(teacher[1] if teacher_ran and teacher else ""), owner=owner)`
    sm.set_audit(&name, &final_verdict, teacher_ran, model, teacher_model, owner);
    log(format!(
        "{name}: flagged — confidence lowered, kept as draft for manual review"
    ));
    json!({
        "skill": name, "result": "flagged", "verdict": verdict, "confidence": 0.35
    })
    .as_object()
    .unwrap()
    .clone()
}

// ===========================================================================
// _run_audit_all_job (skills_routes.py:916-977)
// ===========================================================================

/// `_run_audit_all_job(...)` — audit each named skill in sequence, recording
/// progress in `_skill_audit_jobs[key]`. Cancellation is cooperative (the
/// `cancel` flag is checked at the top of each iteration).
#[allow(clippy::too_many_arguments)]
pub(crate) async fn run_audit_all_job(
    key: &str,
    sm: &SkillsManager,
    names: Vec<String>,
    url: &str,
    model: &str,
    headers: Headers,
    teacher: Option<Teacher>,
    owner: Option<String>,
) {
    // `job = _skill_audit_jobs.get(key); if job is None: return`
    if get_audit_job(key).is_none() {
        return;
    }

    // `log(msg)` — append to job["log"] with a 1000-entry cap.
    let push_log = |msg: String| {
        with_audit_job(key, |job| {
            if let Some(Value::Array(log)) = job.get_mut("log") {
                log.push(Value::String(msg));
                if log.len() > 1000 {
                    let drop = log.len() - 1000;
                    log.drain(0..drop);
                }
            }
        });
    };

    let mut cancelled = false;
    for nm in &names {
        // `if job.get("cancel"): cancelled = True; log("(cancelled)"); break`
        let want_cancel =
            with_audit_job(key, |job| py_truthy(job.get("cancel").unwrap_or(&Value::Null)))
                .unwrap_or(false);
        if want_cancel {
            cancelled = true;
            push_log("(cancelled)".to_string());
            break;
        }
        with_audit_job(key, |job| {
            job.insert("current".to_string(), Value::String(nm.clone()));
        });
        let skills = sm.load(owner.as_deref());
        let sk = match skills.iter().find(|s| skill_name(s) == *nm) {
            Some(s) => s.clone(),
            None => continue,
        };
        // The Rust pipeline has no in-flight cancel/error item, so the Python
        // `except CancelledError` / `except Exception` arms have no source here.
        // audit_one_skill never panics in practice; faithful structure preserved.
        let mut log_fn = |m: String| push_log(m);
        let mut res = audit_one_skill(
            sm,
            &sk,
            url,
            model,
            &headers,
            teacher.as_ref(),
            owner.as_deref(),
            &mut log_fn,
        )
        .await;

        // Attach the refreshed skill_state (9 fields, skills_routes.py:955-965).
        if let Some(refreshed) =
            sm.load(owner.as_deref()).into_iter().find(|s| skill_name(s) == *nm)
        {
            let mut st = Map::new();
            for k in [
                "name",
                "status",
                "confidence",
                "audit_verdict",
                "audit_by_teacher",
                "audit_worker_model",
                "audit_teacher_model",
                "audited_at",
                "necessity",
            ] {
                st.insert(k.to_string(), refreshed.get(k).cloned().unwrap_or(Value::Null));
            }
            res.insert("skill_state".to_string(), Value::Object(st));
        }

        with_audit_job(key, |job| {
            if let Some(Value::Array(results)) = job.get_mut("results") {
                results.push(Value::Object(res.clone()));
                let n = results.len();
                job.insert("done".to_string(), json!(n));
            }
        });
    }

    // finally: current=None; status; finished.
    with_audit_job(key, |job| {
        job.insert("current".to_string(), Value::Null);
        let was_cancel = py_truthy(job.get("cancel").unwrap_or(&Value::Null));
        let status = if cancelled || was_cancel {
            "cancelled"
        } else {
            "done"
        };
        job.insert("status".to_string(), Value::String(status.to_string()));
        job.insert("finished".to_string(), json!(crate::pytime::time()));
    });
}

// ===========================================================================
// _resolve_audit_models (skills_routes.py:979-1013)
// ===========================================================================

/// `_resolve_audit_models()` -> (url, model, headers, teacher) or Err(message).
///
/// SKIPS the `list_model_ids` served-model drift normalization (the Rust
/// `list_model_ids` is a different, network-free string-normalizer; see the
/// module docstring) — byte-equivalent to the Python `except` branch.
pub(crate) async fn resolve_audit_models(
) -> Result<(String, String, Headers, Option<Teacher>), String> {
    let (url, model, headers) = crate::src::endpoint_resolver::resolve_endpoint("utility", None);
    let url = match url {
        Some(u) if !u.is_empty() => u,
        _ => {
            return Err(
                "No model configured — set a Default or Utility model in Settings.".to_string(),
            )
        }
    };
    let model = match model {
        Some(m) if !m.is_empty() => m,
        _ => {
            return Err(
                "No model configured — set a Default or Utility model in Settings.".to_string(),
            )
        }
    };
    let headers = headers.unwrap_or_default();

    // teacher resolution.
    let mut teacher: Option<Teacher> = None;
    let teacher_enabled =
        py_truthy(&crate::src::settings::get_setting("teacher_enabled", json!(false)));
    if teacher_enabled {
        let spec = crate::src::settings::get_setting("teacher_model", json!(""))
            .as_str()
            .unwrap_or("")
            .trim()
            .to_string();
        if !spec.is_empty() {
            match crate::src::ai_interaction::resolve_model_spec(&spec, None).await {
                Ok((t_url, t_model, t_headers)) => {
                    if !t_url.is_empty() && !t_model.is_empty() {
                        teacher = Some((t_url, t_model, t_headers));
                    }
                }
                Err(e) => {
                    logger::warning(&format!("Audit teacher resolve failed: {e}"));
                }
            }
        }
    }
    Ok((url, model, headers, teacher))
}

// ===========================================================================
// run_scheduled_skill_audit (skills_routes.py:1016-1056)
// ===========================================================================

/// `run_scheduled_skill_audit(skills_manager, owner=None, max_skills=8)` — the
/// nightly audit pass. Audits the LEAST-recently-audited skills first, capped.
pub async fn run_scheduled_skill_audit(
    sm: &SkillsManager,
    owner: Option<&str>,
    max_skills: usize,
) -> Value {
    let key = audit_key(owner);
    // `if existing and existing.get("status") == "running": return {running, skipped}`
    if audit_running(&key).is_some() {
        logger::info("Scheduled skill audit skipped — a run is already active.");
        return json!({"status": "running", "skipped": true});
    }

    let (url, model, headers, teacher) = match resolve_audit_models().await {
        Ok(t) => t,
        Err(e) => {
            logger::info(&format!("Scheduled skill audit skipped — {e}"));
            return json!({"status": "skipped", "reason": e});
        }
    };

    let mut skills = sm.load(owner);
    // Oldest-audited first; never-audited (None) sorts to the very front via -1.0.
    skills.sort_by(|a, b| {
        let ax = a.get("audited_at").and_then(|v| v.as_f64()).unwrap_or(-1.0);
        let bx = b.get("audited_at").and_then(|v| v.as_f64()).unwrap_or(-1.0);
        ax.partial_cmp(&bx).unwrap_or(std::cmp::Ordering::Equal)
    });
    let names: Vec<String> = skills
        .iter()
        .map(skill_name)
        .filter(|n| !n.is_empty())
        .take(max_skills.max(1))
        .collect();
    if names.is_empty() {
        return json!({"status": "done", "total": 0});
    }

    // Seed the job (the scheduled scope + nightly log line, skills_routes.py:1045-1052).
    let teacher_suffix = match &teacher {
        Some(t) => format!("; teacher {}", t.1),
        None => String::new(),
    };
    let log_line = format!(
        "Nightly audit of {} least-recently-checked skill(s) with {model}{teacher_suffix}",
        names.len()
    );
    let job = json!({
        "status": "running",
        "scope": "scheduled",
        "model": model,
        "teacher": teacher.as_ref().map(|t| t.1.clone()),
        "total": names.len(),
        "done": 0,
        "current": Value::Null,
        "results": [],
        "log": [log_line],
        "started": crate::pytime::time(),
        "cancel": false,
    });
    SKILL_AUDIT_JOBS
        .lock()
        .expect("SKILL_AUDIT_JOBS poisoned")
        .insert(key.clone(), job);

    logger::info(&format!(
        "Scheduled skill audit starting: {} skill(s) (owner={})",
        names.len(),
        owner.filter(|o| !o.is_empty()).unwrap_or("all")
    ));
    let total = names.len();
    run_audit_all_job(
        &key,
        sm,
        names,
        &url,
        &model,
        headers,
        teacher,
        owner.map(|s| s.to_string()),
    )
    .await;
    let results = get_audit_job(&key)
        .and_then(|j| j.get("results").cloned())
        .unwrap_or_else(|| Value::Array(Vec::new()));
    json!({"status": "done", "total": total, "results": results})
}

// ===========================================================================
// Tests — the pure parsing/task helpers (parity with the Python).
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_test_task_uses_when_to_use_then_falls_back() {
        let mut sk = Map::new();
        sk.insert("when_to_use".to_string(), Value::String("  editing docs ".to_string()));
        let t = skill_test_task(&sk);
        assert!(t.contains("Context for when this skill is used: editing docs"));

        let mut sk2 = Map::new();
        sk2.insert("description".to_string(), Value::String("desc".to_string()));
        assert!(skill_test_task(&sk2).contains(": desc"));

        let mut sk3 = Map::new();
        sk3.insert("name".to_string(), Value::String("nm".to_string()));
        assert!(skill_test_task(&sk3).contains(": nm"));

        let empty = Map::new();
        assert!(skill_test_task(&empty).contains(": (general)"));
    }

    #[test]
    fn parse_verdict_strips_think_and_keeps_last_valid() {
        let raw = "<think>I am reasoning {\"verdict\": \"fail\"} maybe</think>\n\
                   Some prose {\"foo\": 1}\n\
                   {\"verdict\": \"pass\", \"confidence\": 0.9, \"summary\": \"ok\", \"issues\": [\"a\", \"\"]}";
        let v = parse_verdict(raw).expect("should parse");
        assert_eq!(v.get("verdict").unwrap(), "pass");
        assert_eq!(v.get("confidence").unwrap().as_f64().unwrap(), 0.9);
        assert_eq!(v.get("summary").unwrap(), "ok");
        // blank issue dropped.
        assert_eq!(v.get("issues").unwrap().as_array().unwrap().len(), 1);
    }

    #[test]
    fn parse_verdict_trailing_comma_and_clamp() {
        let raw = "{\"verdict\": \"needs_work\", \"confidence\": 5.0, \"summary\": \"x\",}";
        let v = parse_verdict(raw).expect("trailing comma should be cleaned");
        assert_eq!(v.get("verdict").unwrap(), "needs_work");
        // clamped to 1.0
        assert_eq!(v.get("confidence").unwrap().as_f64().unwrap(), 1.0);
    }

    #[test]
    fn parse_verdict_keyword_fallback_from_prose() {
        let raw = "The skill clearly works. verdict: pass — nothing else.";
        let v = parse_verdict(raw).expect("keyword fallback");
        assert_eq!(v.get("verdict").unwrap(), "pass");
        assert_eq!(v.get("confidence").unwrap().as_f64().unwrap(), 0.0);
    }

    #[test]
    fn parse_verdict_unparseable_returns_none() {
        assert!(parse_verdict("no json, no verdict keyword here").is_none());
    }

    #[test]
    fn clip_and_tail_are_codepoint_based() {
        assert_eq!(clip("héllo", 3), "hél");
        assert_eq!(clip_tail("héllo", 2), "lo");
    }

    #[test]
    fn clip_transcript_keeps_head_and_tail() {
        let body: String = "x".repeat(40);
        let s = clip_transcript(&body, 20);
        assert!(s.contains("…[transcript trimmed for length]…"));
        // head = 20/4 = 5; tail = 15
        assert!(s.starts_with("xxxxx\n\n"));
    }

    #[test]
    fn clip_transcript_empty_default() {
        assert_eq!(clip_transcript("   ", 100), "(no output produced)");
    }

    #[test]
    fn should_check_retrieval_precision_broad_tag() {
        let mut sk = Map::new();
        sk.insert("tags".to_string(), json!(["network"]));
        assert!(should_check_retrieval_precision(&sk));

        let mut narrow = Map::new();
        narrow.insert("tags".to_string(), json!(["vimscript-foo"]));
        narrow.insert("name".to_string(), Value::String("a".to_string()));
        assert!(!should_check_retrieval_precision(&narrow));
    }

    #[test]
    fn should_check_retrieval_precision_two_broad_words_in_text() {
        let mut sk = Map::new();
        sk.insert("tags".to_string(), json!([]));
        sk.insert("name".to_string(), Value::String("server".to_string()));
        sk.insert("description".to_string(), Value::String("network helper".to_string()));
        assert!(should_check_retrieval_precision(&sk));
    }

    #[test]
    fn audit_flag_text_flattens_and_lowercases() {
        let d = json!({"a": "Too-Generic", "b": "X"});
        let arr = json!(["TRIVIAL", null]);
        let text = audit_flag_text(&[&d, &arr]);
        assert!(text.contains("too-generic"));
        assert!(text.contains("trivial"));
        assert!(GENERIC_RE.is_match(&text));
    }

    #[test]
    fn generic_blocker_detects_unnecessary_reason() {
        let mut nec = Map::new();
        nec.insert("necessary".to_string(), Value::Bool(false));
        nec.insert("reason".to_string(), Value::String("This is trivial.".to_string()));
        let r = audit_generic_blocker(None, Some(&nec), None);
        assert_eq!(r.as_deref(), Some("This is trivial."));
    }

    #[test]
    fn dup_score_published_dominates() {
        let mut pub_sk = Map::new();
        pub_sk.insert("status".to_string(), Value::String("published".to_string()));
        pub_sk.insert("name".to_string(), Value::String("a".to_string()));
        let mut draft = Map::new();
        draft.insert("status".to_string(), Value::String("draft".to_string()));
        draft.insert("uses".to_string(), json!(10));
        draft.insert("name".to_string(), Value::String("a".to_string()));
        assert!(dup_score(&pub_sk) > dup_score(&draft));
    }

    #[test]
    fn frontmatter_lookahead_keeps_last_doc() {
        // Two doc-starts; only the LAST `^---\n` that has a `name:` ahead wins.
        let text = "prose\n---\nfoo: bar\n---\nname: keep\ndescription: d\n";
        let last_start = FRONTMATTER_START_RE
            .find_iter(text)
            .filter(|m| has_name_line_ahead(text, m.start()))
            .map(|m| m.start())
            .last()
            .unwrap();
        assert!(text[last_start..].starts_with("---\nname: keep"));
    }

    #[test]
    fn frontmatter_lookahead_no_name_yields_none() {
        let text = "---\nfoo: bar\n---\nbaz: qux\n";
        let any = FRONTMATTER_START_RE
            .find_iter(text)
            .any(|m| has_name_line_ahead(text, m.start()));
        assert!(!any);
    }

    #[test]
    fn key_encoding() {
        assert_eq!(test_key(Some("alice"), "foo"), "alice\u{0}foo");
        assert_eq!(test_key(None, "foo"), "\u{0}foo");
        assert_eq!(audit_key(Some("bob")), "bob");
        assert_eq!(audit_key(None), "");
    }
}
