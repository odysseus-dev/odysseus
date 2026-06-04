// src/search/query.rs  <- src/search/query.py
//! Query enhancement, entity extraction, and cache duration helpers.

use crate::pylog as logger;
use chrono::Duration;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
use std::collections::HashSet;

// logger = logging.getLogger(__name__)  -> crate::pylog shim.

// ----------------------------------------------------------------------
// Query processing helpers
// ----------------------------------------------------------------------

/// Return the leading question word if present (who, what, when, where, why, how).
pub fn detect_question_type(query: &str) -> Option<String> {
    let q = query.trim().to_lowercase();
    for word in ["who", "what", "when", "where", "why", "how"] {
        // Require a whole-word match: a bare prefix mis-flags ordinary queries
        // like "whatsapp pricing" (-> what) or "however ..." (-> how), which
        // then get spurious boost terms OR-appended in enhance_query.
        if q == word || q.starts_with(&format!("{} ", word)) {
            return Some(word.to_string());
        }
    }
    None
}

/// `re.findall(r"\b[A-Z][a-zA-Z]+\b", ...)` — capitalized word tokens.
static NAME_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b[A-Z][a-zA-Z]+\b").unwrap());
/// `re.findall(r"\b(?:19|20)\d{2}\b", ...)` — non-capturing group, so Python's
/// `findall` yields the full 4-digit year (`"1999"`/`"2024"`), not a subgroup.
static YEAR_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(?:19|20)\d{2}\b").unwrap());
/// Month/day/year date pattern, `flags=re.I` rendered as inline `(?i)`.
static MONTH_DAY_YEAR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\s+\d{1,2},?\s*\d{4}\b",
    )
    .unwrap()
});

/// Lightweight entity extraction: capitalized words and date patterns.
///
/// Returns a dict-shaped `Value` `{"names": [...], "dates": [...]}`.
pub fn extract_entities(query: &str) -> Value {
    // entities: Dict[str, List[str]] = {"names": [], "dates": []}
    let mut entities = json!({"names": [], "dates": []});
    let qtype = detect_question_type(query);
    let mut cleaned = query.to_string();
    if let Some(qtype) = &qtype {
        // re.sub(rf"^{qtype}\b", "", cleaned, flags=re.I).strip()
        let pat = Regex::new(&format!(r"(?i)^{}\b", regex::escape(qtype))).unwrap();
        cleaned = pat.replace(&cleaned, "").trim().to_string();
    }
    for token in NAME_RE.find_iter(&cleaned) {
        entities["names"]
            .as_array_mut()
            .unwrap()
            .push(Value::String(token.as_str().to_string()));
    }
    for m in YEAR_RE.find_iter(&cleaned) {
        // findall with no capture group yields the whole match (full year).
        entities["dates"]
            .as_array_mut()
            .unwrap()
            .push(Value::String(m.as_str().to_string()));
    }
    let month_day_year: Vec<String> = MONTH_DAY_YEAR_RE
        .find_iter(&cleaned)
        .map(|m| m.as_str().to_string())
        .collect();
    for mdy in month_day_year {
        entities["dates"]
            .as_array_mut()
            .unwrap()
            .push(Value::String(mdy));
    }
    entities
}

/// `re.split(r"\s+and\s+|\s+or\s+|;", query, flags=re.I)` — precompiled, `re.I`
/// rendered as inline `(?i)`.
static SPLIT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\s+and\s+|\s+or\s+|;").unwrap());

/// Split a query into sub-queries on common conjunctions.
pub fn split_multi_part(query: &str) -> Vec<String> {
    // [p.strip() for p in parts if p.strip()]
    SPLIT_RE
        .split(query)
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect()
}

/// `re.search(r"\bsite:([^\s]+)", ...)` with `flags=re.I` -> inline `(?i)`.
static SITE_SEARCH_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\bsite:([^\s]+)").unwrap());
/// `re.sub(r"\bsite:[^\s]+", "", ...)` with `flags=re.I` -> inline `(?i)`.
static SITE_SUB_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\bsite:[^\s]+").unwrap());

/// Detect a 'site:example.com' token. Returns (query_without_token, site_or_None).
pub fn extract_site_filter(query: &str) -> (String, Option<String>) {
    if let Some(m) = SITE_SEARCH_RE.captures(query) {
        let site = m.get(1).unwrap().as_str().to_string();
        let new_query = SITE_SUB_RE.replace_all(query, "").trim().to_string();
        return (new_query, Some(site));
    }
    (query.to_string(), None)
}

/// Append extracted entities to the query using OR to increase relevance.
pub fn boost_entities_in_query(base_query: &str, entities: &Value) -> String {
    let mut parts: Vec<String> = vec![base_query.to_string()];
    // if entities.get("names"):  (Python falsy: missing / empty list)
    if let Some(names) = entities.get("names").and_then(|v| v.as_array()) {
        if !names.is_empty() {
            let joined = names
                .iter()
                .map(|n| format!("\"{}\"", value_to_str(n)))
                .collect::<Vec<_>>()
                .join(" OR ");
            parts.push(joined);
        }
    }
    // if entities.get("dates"):
    if let Some(dates) = entities.get("dates").and_then(|v| v.as_array()) {
        if !dates.is_empty() {
            let joined = dates
                .iter()
                .map(|d| format!("\"{}\"", value_to_str(d)))
                .collect::<Vec<_>>()
                .join(" OR ");
            parts.push(joined);
        }
    }
    parts.join(" ")
}

/// Process the original query: site filter, question type boosts, entity extraction.
pub fn enhance_query(original_query: &str) -> (String, Option<String>) {
    let (query_without_site, site) = extract_site_filter(original_query);
    let sub_queries = split_multi_part(&query_without_site);

    let mut enhanced_subs: Vec<String> = Vec::new();
    for sub in &sub_queries {
        let qtype = detect_question_type(sub);
        let mut boost_keywords: Vec<&str> = Vec::new();
        match qtype.as_deref() {
            Some("who") => boost_keywords.push("person"),
            Some("when") => boost_keywords.push("date"),
            Some("where") => boost_keywords.push("location"),
            Some("why") => boost_keywords.push("reason"),
            Some("how") => boost_keywords.push("method"),
            _ => {}
        }
        let entities = extract_entities(sub);
        let mut boosted = boost_entities_in_query(sub, &entities);
        // if boost_keywords:  (Python falsy: empty list)
        if !boost_keywords.is_empty() {
            boosted = format!("({}) OR ({})", boosted, boost_keywords.join(" OR "));
        }
        enhanced_subs.push(boosted);
    }

    // " AND ".join(f"({s})" for s in enhanced_subs)
    let mut final_query = enhanced_subs
        .iter()
        .map(|s| format!("({})", s))
        .collect::<Vec<_>>()
        .join(" AND ");
    // if site:  (Python falsy: None / empty string)
    if let Some(site) = site.as_deref().filter(|s| !s.is_empty()) {
        final_query = format!("{} site:{}", final_query, site);
    }
    (final_query, site)
}

/// Build an enhanced search query with optional time filtering.
///
/// `time_filter` defaults to `None` in the Python (`= None`).
pub fn build_enhanced_query(query: &str, time_filter: Option<&str>) -> String {
    let (mut enhanced_query, _) = enhance_query(query);

    // if time_filter:  (Python falsy: None / empty string)
    if let Some(time_filter) = time_filter.filter(|s| !s.is_empty()) {
        let time_map: Vec<(&str, &str)> =
            vec![("day", "d"), ("week", "w"), ("month", "m"), ("year", "y")];
        if let Some((_, code)) = time_map.iter().find(|(k, _)| *k == time_filter) {
            enhanced_query = format!("{} after:{}", enhanced_query, code);
            logger::info(&format!("Added time filter '{}' to query", time_filter));
        }
    }

    logger::info(&format!(
        "Enhanced query: '{}' -> '{}'",
        query, enhanced_query
    ));
    enhanced_query
}

// ----------------------------------------------------------------------
// Cache duration helpers
// ----------------------------------------------------------------------

/// `re.findall(r"\b\w+\b", ...)` — word-token extraction.
static WORD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b\w+\b").unwrap());

/// Lightweight heuristic to decide if a query is news-oriented.
pub fn is_news_query(query: &str) -> bool {
    let news_terms: HashSet<&str> = [
        "news", "latest", "breaking", "today", "today's", "current", "updates",
        "happening",
    ]
    .into_iter()
    .collect();
    // tokens = set(re.findall(r"\b\w+\b", query.lower()))
    let lowered = query.to_lowercase();
    let tokens: HashSet<String> = WORD_RE
        .find_iter(&lowered)
        .map(|m| m.as_str().to_string())
        .collect();
    // return bool(tokens & news_terms)  -> non-empty intersection is truthy.
    tokens.iter().any(|t| news_terms.contains(t.as_str()))
}

/// News queries -> 30 minutes, reference queries -> 24 hours.
pub fn cache_duration_for_query(query: &str) -> Duration {
    if is_news_query(query) {
        return Duration::minutes(30);
    }
    Duration::hours(24)
}

/// `str(x)` for a JSON value: raw chars for a string, JSON rendering otherwise.
fn value_to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_question_type_whole_word_only() {
        // Whole-word matches return the question word.
        assert_eq!(detect_question_type("who is napoleon"), Some("who".into()));
        assert_eq!(
            detect_question_type("What happened in 1999"),
            Some("what".into())
        );
        assert_eq!(detect_question_type("how to bake bread"), Some("how".into()));
        // A bare standalone word also matches.
        assert_eq!(detect_question_type("how"), Some("how".into()));
        assert_eq!(detect_question_type("  WHO  "), Some("who".into()));
    }

    #[test]
    fn detect_question_type_no_prefix_misfire() {
        // "whatsapp" must NOT match "what"; "however" must NOT match "how".
        assert_eq!(detect_question_type("whatsapp pricing"), None);
        assert_eq!(detect_question_type("however this works"), None);
        assert_eq!(detect_question_type("whoever said that"), None);
        assert_eq!(detect_question_type("whenever you like"), None);
        assert_eq!(detect_question_type("whereabouts is it"), None);
    }

    #[test]
    fn extract_entities_pushes_full_year() {
        // Years must be the full 4-digit match, not the "19"/"20" prefix.
        let entities = extract_entities("Report from 1999 and 2024");
        let dates: Vec<String> = entities["dates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        assert_eq!(dates, vec!["1999".to_string(), "2024".to_string()]);
    }

    #[test]
    fn extract_entities_year_after_qtype_strip() {
        // Question word stripped, then full year extracted.
        let entities = extract_entities("What happened in 2008");
        let dates: Vec<String> = entities["dates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        assert_eq!(dates, vec!["2008".to_string()]);
    }

    #[test]
    fn enhance_query_no_boost_for_whatsapp() {
        // "whatsapp" should not be treated as a "what" question and so should
        // not get the spurious "OR (...)" boost-keyword clause.
        let (enhanced, site) = enhance_query("whatsapp pricing");
        assert_eq!(site, None);
        assert!(
            !enhanced.contains(" OR ("),
            "unexpected boost clause: {enhanced}"
        );
    }
}
