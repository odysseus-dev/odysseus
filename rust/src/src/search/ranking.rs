// src/search/ranking.rs  <- src/search/ranking.py
//! Search result ranking based on relevance, source quality, and recency.

use crate::pylog as logger;
use chrono::{NaiveDate, NaiveDateTime, Utc};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::Value;
use std::collections::HashSet;

// `logger = logging.getLogger(__name__)` — the module-level logger. The pylog
// shim is a free-function facade; we keep the binding here so references read
// like the Python even though it carries no state.
#[allow(dead_code)]
fn _logger_marker() {
    logger::debug("ranking logger");
}

/// `_NEWS_HINTS`.
static NEWS_HINTS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    ["news", "nyheter", "headlines", "breaking", "latest", "today", "idag"]
        .into_iter()
        .collect()
});

/// `_SPORTS_HINTS`.
static SPORTS_HINTS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "sport", "sports", "soccer", "football", "hockey", "nba", "nfl", "mlb",
        "fifa", "world cup", "championship", "quarterfinal", "eliminates",
    ]
    .into_iter()
    .collect()
});

/// `_SPORTS_HINT_RE` — word-boundary match so "sport" does not fire inside
/// "transport"/"passport" and a domain like "transport.gov" is not mistaken for
/// a sports site. Mirrors
/// `re.compile(r"\b(?:" + "|".join(re.escape(h) for h in _SPORTS_HINTS) + r")\b")`.
///
/// Python iterates `_SPORTS_HINTS` (a set) in arbitrary order when joining the
/// alternation; the order does not affect whether `.search` finds a match (each
/// alternative is independently `\b`-anchored), so we build the pattern from a
/// deterministically sorted list to keep the compiled regex stable.
static SPORTS_HINT_RE: Lazy<Regex> = Lazy::new(|| {
    let mut hints: Vec<&str> = SPORTS_HINTS.iter().copied().collect();
    hints.sort_unstable();
    let alternation = hints
        .iter()
        .map(|h| regex::escape(h))
        .collect::<Vec<_>>()
        .join("|");
    Regex::new(&format!(r"\b(?:{alternation})\b")).unwrap()
});

/// `_LOW_VALUE_NEWS_DOMAINS`.
static LOW_VALUE_NEWS_DOMAINS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "facebook.com", "www.facebook.com", "sports.yahoo.com", "yahoo.com",
        "www.yahoo.com", "msn.com", "www.msn.com",
    ]
    .into_iter()
    .collect()
});

/// `_TRUSTED_NEWS_DOMAINS`.
static TRUSTED_NEWS_DOMAINS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "apnews.com", "www.apnews.com", "reuters.com", "www.reuters.com",
        "bbc.com", "www.bbc.com", "cbc.ca", "www.cbc.ca",
        "ctvnews.ca", "www.ctvnews.ca", "globalnews.ca", "www.globalnews.ca",
        "theguardian.com",
        "www.theguardian.com", "euronews.com", "www.euronews.com",
        "dw.com", "www.dw.com", "government.se", "www.government.se",
    ]
    .into_iter()
    .collect()
});

/// `re.findall(r"\b\w+\b", ...)` — precompiled once. `\w` in the Python `re`
/// module is Unicode-aware by default; the Rust `regex` crate's `\w` is also
/// Unicode-aware, so the token set matches.
static WORD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b\w+\b").unwrap());

/// `urlparse(url).netloc.lower()`.
///
/// `urllib.parse.urlparse` returns the authority (`netloc`) — the substring
/// between the `//` after the scheme and the first `/`, `?`, or `#`. With no
/// `//` present, `netloc` is `""`. We reproduce that parse directly so the
/// host:port (including userinfo, if any) is captured verbatim before
/// lowercasing.
fn _domain(url: &str) -> String {
    // urlparse essentially never raises for str input, but the Python wraps it
    // in try/except returning "" on failure; the parse below is infallible.
    let rest = match url.split_once("://") {
        Some((_scheme, rest)) => rest,
        None => {
            // Without "scheme://" there is no netloc (mirrors urlparse, which
            // only treats the part after "//" as netloc).
            return String::new();
        }
    };
    // netloc ends at the first '/', '?', or '#'.
    let end = rest
        .find(['/', '?', '#'])
        .unwrap_or(rest.len());
    rest[..end].to_lowercase()
}

/// `title_score(title)` — fraction of query terms matched as whole words.
fn title_score(title: &str, query_terms: &[String]) -> f64 {
    // if not title:
    if title.is_empty() {
        return 0.0;
    }
    let title_lc = title.to_lowercase();
    // matches = sum(1 for term in query_terms if re.search(rf"\b{re.escape(term)}\b", title_lc))
    let matches = query_terms
        .iter()
        .filter(|term| {
            let pattern = format!(r"\b{}\b", regex::escape(term));
            Regex::new(&pattern).unwrap().is_match(&title_lc)
        })
        .count();
    // return matches / len(query_terms) if query_terms else 0.0
    if !query_terms.is_empty() {
        matches as f64 / query_terms.len() as f64
    } else {
        0.0
    }
}

/// `snippet_score(snippet)` — length factor combined with term-hit factor.
fn snippet_score(snippet: &str, query_terms: &[String]) -> f64 {
    // if not snippet:
    if snippet.is_empty() {
        return 0.0;
    }
    // length_factor = min(len(snippet), 200) / 200
    // Python len() counts Unicode code points, not bytes.
    let length_factor = std::cmp::min(snippet.chars().count(), 200) as f64 / 200.0;
    let snippet_lc = snippet.to_lowercase();
    // term_hits = sum(1 for term in query_terms if term in snippet.lower())
    let term_hits = query_terms.iter().filter(|term| snippet_lc.contains(term.as_str())).count();
    // term_factor = term_hits / len(query_terms) if query_terms else 0.0
    let term_factor = if !query_terms.is_empty() {
        term_hits as f64 / query_terms.len() as f64
    } else {
        0.0
    };
    (length_factor + term_factor) / 2.0
}

/// `domain_score(url)` — authority weight by netloc / TLD.
fn domain_score(url: &str) -> f64 {
    let netloc = _domain(url);
    // if not netloc:
    if netloc.is_empty() {
        return 0.0;
    }
    if TRUSTED_NEWS_DOMAINS.contains(netloc.as_str()) {
        return 1.0;
    }
    if netloc.ends_with(".edu") || netloc.ends_with(".gov") {
        return 1.0;
    }
    if netloc.ends_with(".org") {
        return 0.7;
    }
    0.4
}

/// `recency_score(age_str)` — decays linearly between 7 and 30 days old.
fn recency_score(age_str: Option<&str>) -> f64 {
    // if not age_str:  (None or empty string are both falsy)
    let age_str = match age_str {
        Some(s) if !s.is_empty() => s,
        _ => return 0.0,
    };
    // for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
    //     try: dt = datetime.strptime(age_str, fmt); break
    //     except Exception: dt = None
    let mut dt: Option<NaiveDateTime> = None;
    for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"] {
        // datetime.strptime with a date-only format ("%Y-%m-%d") yields a
        // datetime at midnight; NaiveDateTime::parse_from_str rejects the
        // date-only string, so parse it as a NaiveDate and lift to midnight.
        let parsed = if fmt == "%Y-%m-%d" {
            NaiveDate::parse_from_str(age_str, fmt)
                .ok()
                .and_then(|d| d.and_hms_opt(0, 0, 0))
        } else {
            NaiveDateTime::parse_from_str(age_str, fmt).ok()
        };
        match parsed {
            Some(parsed) => {
                dt = Some(parsed);
                break;
            }
            None => {
                dt = None;
            }
        }
    }
    // if not dt:
    let dt = match dt {
        Some(dt) => dt,
        None => return 0.0,
    };
    // now = now or _utcnow_naive()
    // _utcnow_naive() == datetime.now(timezone.utc).replace(tzinfo=None): the age
    // is measured against UTC, not local time, so the result is independent of the
    // host's UTC offset and matches the UTC-style published dates parsed above.
    let now = Utc::now().naive_utc();
    // timedelta.days is the floor of the duration toward negative infinity.
    let days_old = (now - dt).num_days();
    if days_old <= 7 {
        return 1.0;
    }
    if days_old >= 30 {
        return 0.0;
    }
    (30 - days_old) as f64 / 23.0
}

/// `news_quality_adjustment(title, snippet, url)` — bonus/penalty tuning for
/// news-style queries.
fn news_quality_adjustment(
    title: &str,
    snippet: &str,
    url: &str,
    is_news_query: bool,
    is_sports_query: bool,
    query_terms: &[String],
) -> f64 {
    // if not is_news_query:
    if !is_news_query {
        return 0.0;
    }
    // text = f"{title} {snippet}".lower()
    let text = format!("{title} {snippet}").to_lowercase();
    let netloc = _domain(url);
    let mut adjustment = 0.0_f64;
    if TRUSTED_NEWS_DOMAINS.contains(netloc.as_str()) {
        adjustment += 1.2;
    }
    if ["latest news", "breaking news", "daily coverage", "news from"]
        .iter()
        .any(|term| text.contains(term))
    {
        adjustment += 0.4;
    }
    if LOW_VALUE_NEWS_DOMAINS.contains(netloc.as_str()) {
        adjustment -= 0.8;
    }
    // if not is_sports_query and (_SPORTS_HINT_RE.search(text) or _SPORTS_HINT_RE.search(netloc)):
    if !is_sports_query && (SPORTS_HINT_RE.is_match(&text) || SPORTS_HINT_RE.is_match(&netloc)) {
        adjustment -= 1.5;
    }
    // A country/news query should not rank a page whose title/snippet barely
    // mentions the country above actual news pages for that country.
    // subject_terms = [t for t in query_terms if t not in _NEWS_HINTS]
    let subject_terms: Vec<&String> = query_terms
        .iter()
        .filter(|t| !NEWS_HINTS.contains(t.as_str()))
        .collect();
    if !subject_terms.is_empty()
        && !subject_terms
            .iter()
            .any(|t| text.contains(t.as_str()) || netloc.contains(t.as_str()))
    {
        adjustment -= 1.0;
    }
    adjustment
}

/// Rank search results by title relevance, snippet quality, domain authority, and recency.
///
/// `results` is the list of result dicts (`title`/`snippet`/`url`/`age`) and the
/// return value is the same dicts reordered by descending score.
pub fn rank_search_results(query: &str, results: Vec<Value>) -> Vec<Value> {
    // query_terms = [t.lower() for t in re.findall(r"\b\w+\b", query)]
    let query_terms: Vec<String> = WORD_RE
        .find_iter(query)
        .map(|m| m.as_str().to_lowercase())
        .collect();
    // query_lc = query.lower()
    let query_lc = query.to_lowercase();
    // is_news_query = any(term in _NEWS_HINTS for term in query_terms)
    let is_news_query = query_terms.iter().any(|term| NEWS_HINTS.contains(term.as_str()));
    // is_sports_query = bool(_SPORTS_HINT_RE.search(query_lc))
    let is_sports_query = SPORTS_HINT_RE.is_match(&query_lc);

    // ranked = []
    let mut ranked: Vec<(f64, Value)> = Vec::new();
    for result in results.into_iter() {
        // title = result.get("title", "")
        let title = result.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
        // snippet = result.get("snippet", "")
        let snippet = result.get("snippet").and_then(|v| v.as_str()).unwrap_or("").to_string();
        // url = result.get("url", "")
        let url = result.get("url").and_then(|v| v.as_str()).unwrap_or("").to_string();
        // age = result.get("age", None)
        let age = result.get("age").and_then(|v| v.as_str());

        // score = (
        //     2.0 * title_score(title)
        //     + 1.0 * snippet_score(snippet)
        //     + 1.5 * domain_score(url)
        //     + 1.0 * recency_score(age)
        //     + news_quality_adjustment(title, snippet, url)
        // )
        let score = 2.0 * title_score(&title, &query_terms)
            + 1.0 * snippet_score(&snippet, &query_terms)
            + 1.5 * domain_score(&url)
            + 1.0 * recency_score(age)
            + news_quality_adjustment(
                &title,
                &snippet,
                &url,
                is_news_query,
                is_sports_query,
                &query_terms,
            );
        // ranked.append((score, result))
        ranked.push((score, result));
    }

    // ranked.sort(key=lambda x: x[0], reverse=True)
    // Python's list.sort is stable; sort descending by score only (results with
    // equal scores keep their original relative order). `sort_by` is stable in
    // Rust, and we compare on score alone so ties are preserved.
    ranked.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    // return [r for _, r in ranked]
    ranked.into_iter().map(|(_, r)| r).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;
    use serde_json::json;

    // A date `days_ago` days before the current naive-UTC instant, formatted as
    // "%Y-%m-%d %H:%M:%S" so recency_score parses it via the third _AGE_FORMATS
    // entry. Anchoring on Utc::now().naive_utc() (not local time) means the test
    // computes the same "now" the function does regardless of the host TZ.
    fn age_str_days_ago(days_ago: i64) -> String {
        let dt = Utc::now().naive_utc() - Duration::days(days_ago);
        dt.format("%Y-%m-%d %H:%M:%S").to_string()
    }

    #[test]
    fn recency_score_buckets_are_measured_against_utc() {
        // <= 7 days old -> 1.0
        assert_eq!(recency_score(Some(&age_str_days_ago(0))), 1.0);
        assert_eq!(recency_score(Some(&age_str_days_ago(7))), 1.0);
        // >= 30 days old -> 0.0
        assert_eq!(recency_score(Some(&age_str_days_ago(30))), 0.0);
        assert_eq!(recency_score(Some(&age_str_days_ago(45))), 0.0);
        // Linear decay between: 15 days old -> (30 - 15) / 23.
        let mid = recency_score(Some(&age_str_days_ago(15)));
        assert!((mid - (30.0 - 15.0) / 23.0).abs() < 1e-9, "mid = {mid}");
    }

    #[test]
    fn recency_score_empty_and_unparseable_are_zero() {
        assert_eq!(recency_score(None), 0.0);
        assert_eq!(recency_score(Some("")), 0.0);
        assert_eq!(recency_score(Some("not a date")), 0.0);
    }

    #[test]
    fn recency_score_utc_not_skewed_by_offset() {
        // A timestamp exactly "now" in UTC must land in the freshest bucket. With
        // the old Local::now() this could regress across UTC offsets near the day
        // boundary; against UTC it is deterministic.
        let now = Utc::now().naive_utc().format("%Y-%m-%dT%H:%M:%S").to_string();
        assert_eq!(recency_score(Some(&now)), 1.0);
    }

    #[test]
    fn sports_hint_regex_requires_word_boundaries() {
        // True positives: the hint stands alone as a word.
        assert!(SPORTS_HINT_RE.is_match("local sport coverage"));
        assert!(SPORTS_HINT_RE.is_match("nba finals"));
        assert!(SPORTS_HINT_RE.is_match("the world cup is on"));
        // False positives that the old substring check would have triggered:
        // "sport" inside "transport"/"passport".
        assert!(!SPORTS_HINT_RE.is_match("public transport schedule"));
        assert!(!SPORTS_HINT_RE.is_match("renew your passport today"));
    }

    #[test]
    fn is_sports_query_uses_word_boundaries() {
        // "transport" must NOT be classified as a sports query (substring false
        // positive avoided), but "sport" alone must.
        let transport = rank_search_results("transport news today", vec![]);
        assert!(transport.is_empty()); // smoke: no panic, empty in -> empty out

        // Build two news results: one whose text mentions sports (standalone),
        // and one that does not. Under a non-sports query, the sports mention
        // incurs a -1.5 penalty, ranking the non-sports result first.
        let sporty = json!({
            "title": "Latest news",
            "snippet": "soccer match recap and football scores",
            "url": "https://example.org/a",
            "age": null,
        });
        let plain = json!({
            "title": "Latest news",
            "snippet": "transport policy update for the city",
            "url": "https://example.org/b",
            "age": null,
        });
        // News query ("news") but not a sports query.
        let ranked = rank_search_results(
            "news transport",
            vec![sporty.clone(), plain.clone()],
        );
        // The "transport" snippet must not be penalised as sports; the genuine
        // "soccer"/"football" snippet is. So `plain` outranks `sporty`.
        assert_eq!(ranked[0]["url"], "https://example.org/b");
        assert_eq!(ranked[1]["url"], "https://example.org/a");
    }

    #[test]
    fn news_quality_sports_penalty_word_boundary() {
        // is_news_query via "news"; not a sports query. A netloc containing
        // "transport" must NOT trigger the sports penalty (old substring bug),
        // while a snippet with standalone "hockey" must.
        let no_penalty = news_quality_adjustment(
            "City update",
            "public transport report",
            "https://transport.gov/x",
            true,  // is_news_query
            false, // is_sports_query
            &["news".to_string(), "city".to_string()],
        );
        let with_penalty = news_quality_adjustment(
            "City update",
            "hockey game tonight",
            "https://example.com/x",
            true,
            false,
            &["news".to_string(), "city".to_string()],
        );
        // The sports penalty is -1.5; the only difference between the two calls is
        // the sports detection, so they must differ by exactly 1.5.
        assert!((no_penalty - with_penalty - 1.5).abs() < 1e-9,
            "no_penalty={no_penalty} with_penalty={with_penalty}");
    }
}
