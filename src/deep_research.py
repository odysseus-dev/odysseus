# src/deep_research.py
"""
IterResearch-style deep research engine.

Implements an iterative Think→Search→Extract→Synthesize loop where the LLM
drives every decision: what to search, what's relevant, what's missing, and
when to stop.  Inspired by Alibaba's IterResearch approach.
"""
import asyncio
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

from src.research_utils import strip_thinking, is_low_quality
from src.research_navigator import ResearchAction, ResearchNavigator, ResearchPage, assess_source, parse_research_actions

from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)


def current_date_context() -> str:
    """Preamble that grounds research LLMs in the real current
    date. Without it the model falls back to its training-cutoff year and emits
    queries like "best Python tutorials 2025" when the year is actually 2026.
    System TZ-local so it matches what the user sees. Portable strftime only."""
    now = datetime.now().astimezone()
    return (
        f"Today's date is {now.strftime('%B %d, %Y')} ({now.strftime('%Y-%m-%d')}). "
        f"When a search query needs a year or refers to 'latest'/'current'/"
        f"'this year', use {now.strftime('%Y')} or relative wording — never a "
        f"year inferred from training data.\n\n"
        "Assess source publication dates relative to today's date, not your "
        "training cutoff. A recent publication year alone does not show that "
        "a source is future-dated or fabricated.\n\n"
    )

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
RESEARCH_PLAN_PROMPT = """\
You are a research strategist. Before searching, analyze this question and create a research plan.

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
"""

QUERY_GEN_PROMPT = """\
You are a research assistant planning web searches.

**Original question:** {question}

**Research plan:**
{research_plan}

**What we know so far:**
{report}

**Round:** {round_num}

Generate {num_queries} focused search queries that will help answer the question.
{round_instruction}
Use concise keyword queries of about 3-8 content words, not full sentences.
Preserve essential technical names and search a different sub-topic in each query.
Prioritize explicitly named sources with a dedicated query using the exact project or paper name.
Do not add a year unless the question explicitly needs a date or current information.
Do not introduce unrequested hardware, platforms, or other assumptions.

Return ONLY a JSON array of query strings, nothing else.
Example: ["query one", "query two", "query three"]
"""

SMALL_MODEL_QUERY_GEN_PROMPT = """\
You choose web searches for a research task.

Today: {today}
Question: {question}
Round: {round_num}

Return ONLY a JSON array containing {num_queries} short search-query strings.
Use the question's exact topic. Do not explain your answer.
Use concise keyword queries of about 3-8 content words, not full sentences.
Preserve essential technical names and search a different sub-topic in each query.
Prioritize explicitly named sources with a dedicated query using the exact project or paper name.
Do not add a year unless the question explicitly needs a date or current information.
Do not introduce unrequested hardware, platforms, or other assumptions.
Example: ["topic latest news", "topic official sources"]
"""

RESEARCH_ACTION_PROMPT = """\
You are controlling a bounded research navigator. Choose the next actions that will best answer the user's question.

**Original question:** {question}

**Research plan:**
{research_plan}

**What we know so far:**
{report}

**Evidence/source state:**
{source_state}

**Structured source coverage JSON:**
{source_coverage_json}

**Recent navigation observations:**
{navigation_trace}

**Already visited URLs:**
{visited_urls}

**Round:** {round_num}

Available actions:
- web_search: broad/current discovery. Args: {{"query": "focused search query"}}
- web_fetch: read a specific known URL/domain as text. Args: {{"url": "https://..."}}
- browser_read: read a JS-heavy or interaction-heavy specific URL using the private browser. Args: {{"url": "https://..."}}
- browser_snapshot: inspect a rendered page when layout/visual browser state matters. Args: {{"url": "https://..."}}
- private_browser: actual browser tool alias; Research only supports read/open/snapshot with a URL. Args: {{"action": "read", "url": "https://..."}}

Rules:
- For web_search, use concise keyword queries of about 3-8 content words, not full sentences.
- Preserve essential technical names and search a different sub-topic in each query.
- Prioritize explicitly named sources with a dedicated query using the exact project or paper name.
- Do not add a year unless the question explicitly needs a date or current information.
- Do not introduce unrequested hardware, platforms, or other assumptions.
- If the user's latest wording is a meta request like "search this" or "can you search", infer the real topic from the original question, research plan, and report; never use the meta request itself as the search query.
- Prefer web_search for open questions or when you need discovery.
- Prefer web_fetch for official/source URLs you already know.
- Prefer browser_read/browser_snapshot/private_browser only when a page likely needs JS/browser state, rendered layout, or when a prior text fetch was weak.
- If primary/official evidence is missing, search for or fetch source-owned pages.
- If source diversity is thin, search a different angle instead of reusing the same source cluster.
- Do not repeat failed/no-result actions unless you change the query or URL meaningfully.
- If web_fetch failed or found no readable text for an important URL, try browser_read or browser_snapshot.
- Choose at most {max_actions} actions.
- Do not use search engines directly through browser_read.
- Do not repeat visited URLs.
- Return ONLY JSON: {{"actions": [{{"tool": "web_search", "query": "..."}}, {{"tool": "web_fetch", "url": "https://..."}}]}}
"""

SYNTHESIZE_PROMPT = """\
You are updating an evolving research report.

**Original question:** {question}

**Current report:**
{report}

**New findings from this round:**
{new_findings}

**Evidence/source state:**
{source_state}

**Structured source coverage JSON:**
{source_coverage_json}

**Recent navigation observations:**
{navigation_trace}

Integrate the new findings into the existing report. Produce an updated, well-organized \
report that answers the original question as completely as possible given all evidence so far. \
Remove redundancy, resolve contradictions, and maintain logical flow. \
Keep source URLs as inline citations where relevant.
Use only claims supported by the collected source evidence. Preserve each source's
scope and distinguish a demonstrated configuration from a general requirement.
Do not invent hardware minimums, software requirements, training times, or universal
recommendations. Label inferences and unknowns explicitly. A library used in one
example is not mandatory for all workflows. One failed synthetic-data experiment
does not establish that all synthetic data should be avoided.
Prefer claims supported by primary/official \
sources, call out weak or missing evidence plainly, and avoid overconfident conclusions when the \
navigation trace shows failed searches, failed fetches, or thin source diversity.

Write only the updated report — no preamble or meta-commentary.
"""

STOP_PROMPT = """\
You are deciding whether a research report is comprehensive enough.
This step evaluates coverage and support in the provided report. It does not
verify whether a cited source exists: that requires fetching the source, and
cannot be inferred from a title or publication year unfamiliar to you.

**Original question:** {question}

**Current report:**
{report}

**Evidence/source state:**
{source_state}

**Structured source coverage JSON:**
{source_coverage_json}

**Rounds completed:** {round_num} of {max_rounds}

Based on the report so far, do we have enough information to answer the question \
comprehensively?  Consider:
- Are the key aspects of the question addressed?
- Are there obvious gaps or unanswered sub-questions?
- Is the evidence sufficient and from multiple sources?
- Does the evidence include primary/official sources where they should exist?

Use the authoritative current date supplied with this request. Assess coverage
and supported claims, rather than whether a source is familiar from your training
data. A publication newer than your training cutoff can be valid. Do not claim
that a source is future-dated or nonexistent solely because its year is recent;
compare its actual date with the supplied current date.

If rounds completed is well below the target, prefer continuing unless the \
report is already exhaustive.
Explain a concrete coverage gap or why the evidence covers the question. Do not
replace that assessment with an unsupported claim that cited sources do not exist.

Reply with ONLY "YES" or "NO" followed by a brief one-sentence reason.
Example: "YES — The report covers all major aspects with evidence from multiple sources."
Example: "NO — We still lack information about the economic impact."
"""

FINAL_REPORT_PROMPT = """\
Write a **long, detailed, comprehensive** research report answering this question:

**Question:** {question}

**All collected evidence and analysis:**
{report}

**Evidence/source state:**
{source_state}

**Structured source coverage JSON:**
{source_coverage_json}

**Recent navigation observations:**
{navigation_trace}

Requirements:
- Write at MINIMUM 1500 words — this should be a thorough, magazine-quality article
- Use clear ## headings and ### subheadings to organize into logical sections
- Each section should have multiple detailed paragraphs, not just bullet points
- Synthesize and analyze the information — explain WHY things matter, draw comparisons, provide context
- Include specific data points, numbers, and statistics from the evidence
- Include source URLs as inline citations [like this](url)
- Note where sources agree and where they disagree
- Prefer primary/official evidence over commercial, community, or roundup sources
- If evidence is weak, missing, or only secondary, say that directly instead of filling gaps with guesses
- Add a brief executive summary at the top
- End with a clear conclusion that directly answers the question
- Write in an engaging, informative style — not dry or robotic
- Ground factual claims and recommendations in the cited evidence; preserve the scope of each source
- Distinguish examples from requirements, sequential device handoffs from simultaneous multi-device training, and inference from training
- Do not invent hardware minimums, software versions, training times, or mandatory libraries; state when these are unknown
- Label estimates and inferences explicitly, and do not generalize one experiment into a universal recommendation
- Evidence takes priority over length or format: omit unsupported details instead of filling gaps
"""

RESEARCH_EXTRACTOR_PROMPT = """\
Extract source-grounded information from the supplied webpage for this research goal:
{goal}

First decide whether the page directly provides useful evidence for any part of
the goal. Shared words, unrelated scientific uses of a term, and imagined
analogies do not establish relevance. Use only information in this page; do not
fill gaps from your own knowledge or infer hardware requirements from an example.
Preserve qualifications and distinguish sequential hardware use from simultaneous
training. Keep evidence concise, quoting the relevant source passages.

Return a JSON object with "relevant" (a boolean), "rational", "evidence", and
"summary". For an unrelated page, set "relevant": false and both "evidence"
and "summary" to empty strings. For a relevant page, set "relevant": true and
provide nonempty source evidence and a concise summary explaining how it answers
the goal. Do not turn an explanation of irrelevance into a finding.
"""

_RESEARCH_IRRELEVANCE_MARKERS = (
    "completely irrelevant to the research goal",
    "completely irrelevant to the user's goal",
    "irrelevant to the specified research goal",
    "cannot be used to answer the research goal",
    "zero overlap with the topic",
)


def _unusable_research_text(text: str) -> bool:
    return is_low_quality(text) or any(
        marker in text.lower() for marker in _RESEARCH_IRRELEVANCE_MARKERS
    )

_META_SEARCH_QUERIES = {
    "search",
    "search this",
    "can you search",
    "can you search this",
    "please search",
    "look it up",
    "look this up",
    "web search",
    "use web",
    "search online",
}


_PRODUCT_NOUNS = (
    "camera", "computer", "gpu", "hardware", "headphone", "laptop",
    "monitor", "phone", "router", "speaker", "tablet", "tv",
)


def _infer_research_category(question: str) -> Optional[str]:
    """Resolve strong format intent locally before asking the classifier LLM."""
    text = re.sub(r"\s+", " ", str(question or "").strip().lower())
    if not text:
        return None
    if re.search(
        r"\b(fact[ -]?check|debunk|is (?:it|this|that) true|verify (?:the )?claim|"
        r"does .{1,80} really|evidence (?:for|against) (?:the )?claim)\b",
        text,
    ):
        return "factcheck"
    if re.search(
        r"\b(compare|comparison|versus|differences? between|which is better|"
        r"(?:pros and cons|advantages and disadvantages) of .{1,80} (?:and|versus|vs)|"
        r"alternatives? to)\b",
        text,
    ) or " vs " in text:
        return "comparison"
    if re.search(
        r"\b(where (?:can|should) i buy|what should i buy|buying guide|shopping guide|"
        r"product recommendations?|recommend (?:a|an|the) .{1,50} (?:to buy|under|for my))\b",
        text,
    ):
        return "product"
    if re.search(r"\b(best|top)\b", text) and any(noun in text for noun in _PRODUCT_NOUNS):
        return "product"
    if re.search(
        r"\b(how to|how (?:can|do|should) (?:i|we|you)|step[ -]?by[ -]?step|tutorial|"
        r"setup guide|install guide|configuration guide|configure guide|walk me through|"
        r"instructions? (?:for|to))\b",
        text,
    ):
        return "howto"
    return None


def _is_meta_search_query(query: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(query or "").strip().lower().strip("!.? "))
    return normalized in _META_SEARCH_QUERIES


CATEGORY_PROMPTS = {
    "product": """IMPORTANT FORMAT OVERRIDE — this is a PRODUCT research report:
- Structure as a RANKED LIST of products/options (best first)
- For EACH product include: name as ### heading, approximate price, 2-3 sentence summary, **Pros:** bullet list, **Cons:** bullet list, **Where to buy:** URLs as links
- Start with a quick-compare markdown table of top picks (columns: Name, Price, Best For, Rating)
- End with a ## Verdict section picking Best Overall and Best Value
- Still include source citations inline""",

    "comparison": """IMPORTANT FORMAT OVERRIDE — this is a COMPARISON report:
- Create a ## Comparison Table as a markdown table comparing ALL options across key criteria (rows = criteria, columns = options)
- Use checkmarks, ratings, or short values in cells
- Write a ## section per option with its strengths, weaknesses, and ideal use case
- End with ## Best For verdicts (e.g., "**Best for small teams:** Option A because...")
- Include a ## Shared Considerations section for things that apply to all options""",

    "howto": """IMPORTANT FORMAT OVERRIDE — this is a HOW-TO guide:
- Start with ## Quick Guide — a super concise numbered list (one line per step, no details, just the action). Example: 1. Install X  2. Run Y  3. Configure Z
- Then ## Prerequisites listing what's needed before starting
- Then the detailed steps: ## Step 1: ..., ## Step 2: ...
- Each step should have a clear heading and detailed instructions
- Use blockquotes (> ) for tips and warnings: > **Tip:** ... or > **Warning:** ...
- End with ## Common Mistakes section
- Add estimated time and difficulty only when supported by the evidence; otherwise state that they depend on the workload""",

    "factcheck": """IMPORTANT FORMAT OVERRIDE — this is a FACT-CHECK report:
- Start with ## The Claim restating what's being checked
- Create ## Evidence For and ## Evidence Against sections
- Each piece of evidence should be a ### with source name, what it found, and how strong the evidence is
- Include a ## Verdict section with one of: **Supported**, **Mixed Evidence**, or **Unsupported**
- End with ## Nuance & Caveats for important context and limitations
- Be balanced and cite sources for every claim""",

}

# ---------------------------------------------------------------------------
# DeepResearcher
# ---------------------------------------------------------------------------
class DeepResearcher:
    """
    Iterative research engine following the IterResearch pattern.

    Each round: LLM generates queries → SearXNG search → LLM extracts from
    top pages → LLM synthesizes into evolving report → LLM decides continue/stop.
    """

    def __init__(
        self,
        llm_endpoint: str,
        llm_model: str,
        llm_headers: Optional[Dict] = None,
        max_rounds: int = 8,
        max_time: int = 300,
        max_urls_per_round: int = 3,
        max_content_chars: int = 15000,
        max_report_tokens: int = 8192,
        extraction_timeout: int = 90,
        planning_timeout: int = 90,
        query_timeout: int = 120,
        extraction_concurrency: int = 3,
        min_rounds: int = 2,
        max_empty_rounds: int = 2,
        synthesis_window: int = 10,
        progress_callback: Optional[Callable] = None,
        search_provider: Optional[str] = None,
        category: Optional[str] = None,
        session_id: str = "",
    ):
        self.llm_endpoint = llm_endpoint
        self.llm_model = llm_model
        self.simple_research_mode = self._looks_like_small_local_model(llm_model)
        self.llm_headers = llm_headers
        self.search_provider_override = search_provider
        self.category = category
        self.session_id = session_id
        self.max_rounds = max_rounds
        self.max_time = max_time
        self.max_urls_per_round = max_urls_per_round
        self.max_content_chars = max_content_chars
        self.max_report_tokens = max_report_tokens
        self.extraction_timeout = min(3600, max(15, int(extraction_timeout or 90)))
        self.planning_timeout = min(3600, max(15, int(planning_timeout or 90)))
        self.query_timeout = min(3600, max(15, int(query_timeout or 120)))
        self.extraction_concurrency = min(12, max(1, int(extraction_concurrency or 3)))
        self.min_rounds = min_rounds
        self.max_empty_rounds = max_empty_rounds
        self.synthesis_window = synthesis_window
        self._progress = progress_callback
        self._cancelled = False
        self._start_time: float = 0
        self.queries_used: Set[str] = set()
        self.urls_fetched: Set[str] = set()
        self.analyzed_urls: List[Dict[str, str]] = []
        self.round_count: int = 0
        # Track which search providers actually returned results during the
        # run, in arrival order — surfaced in the visual report so users can
        # see whether searxng / brave / tavily etc. carried the work.
        self.providers_used: List[str] = []
        self.findings: List[Dict] = []
        self.evolving_report: str = ""
        self.research_plan: str = ""
        self.failure_stage: str = ""
        self.failure_message: str = ""
        self._search_errors: List[str] = []
        self.action_trace: List[Dict[str, object]] = []
        self.navigation_trace: List[Dict[str, object]] = []
        self.navigator = ResearchNavigator(
            progress_callback=progress_callback,
            search_provider=search_provider,
            session_id=session_id,
        )

    def cancel(self):
        """Request cooperative cancellation of the research loop."""
        self._cancelled = True

    @staticmethod
    def _looks_like_small_local_model(model: str) -> bool:
        """Recognize model names that commonly need a lower-complexity loop."""
        name = str(model or "").lower()
        for match in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s*b(?!\w)", name):
            try:
                if 0 < float(match.group(1)) <= 10:
                    return True
            except ValueError:
                continue
        return bool(
            any(marker in name for marker in ("odysseus", "heretic", "trial55"))
        )

    @staticmethod
    def _looks_like_simple_fact_question(question: str) -> bool:
        """Recognize questions that do not need iterative report writing."""
        text = re.sub(r"\s+", " ", str(question or "").strip().lower())
        return bool(re.match(
            r"^(?:where is|what is|who is|when was|when is|how many|how far is)\b",
            text,
        ))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def research(
        self,
        question: str,
        prior_report: str = "",
        prior_findings: Optional[List[Dict]] = None,
        prior_urls: Optional[Set[str]] = None,
    ) -> str:
        """Run iterative research and return a final report.

        Args:
            question: The research question.
            prior_report: Previous report to continue from (for follow-up research).
            prior_findings: Previous findings to build on.
            prior_urls: URLs already visited (won't be re-fetched).
        """
        self._start_time = time.time()
        self.fast_fact_mode = (
            self.simple_research_mode and self._looks_like_simple_fact_question(question)
        )
        if self.fast_fact_mode:
            # A small local model spends most of its time on synthesis rather
            # than retrieval for simple factual questions. One search round
            # with a compact deterministic report is both faster and safer.
            self.max_rounds = min(self.max_rounds, 1)
            self.min_rounds = 1
            self.extraction_concurrency = min(self.extraction_concurrency, 2)
            logger.info("Using fast factual research path for small model %s", self.llm_model)
        findings: List[Dict] = list(prior_findings) if prior_findings else []
        report = prior_report or ""

        # PLAN: Analyze the question and create a research strategy
        if not prior_report:
            self._emit(phase="planning")
            if self.simple_research_mode:
                self.research_plan = (
                    "Use direct web searches for the user's question and gather "
                    "current, source-backed evidence."
                )
                logger.info("Using simplified research loop for model %s", self.llm_model)
            else:
                self.research_plan = await self._create_plan(question)
            logger.info(f"Research plan: {self.research_plan[:200]}")
        else:
            # Continuation — plan around the follow-up
            self._emit(phase="planning")
            if self.simple_research_mode:
                self.research_plan = (
                    "Use direct web searches for the user's question and gather "
                    "current, source-backed evidence."
                )
            else:
                self.research_plan = await self._create_plan(question)
            logger.info(f"Continuation plan: {self.research_plan[:200]}")
        if not self.category and not prior_report and not self.simple_research_mode:
            self.category = await self._classify_category(question, self.research_plan)
            if self.category:
                logger.info(f"Auto-detected category: {self.category}")

        if prior_urls:
            self.urls_fetched.update(prior_urls)
        self.findings = findings  # expose for handler
        consecutive_empty_rounds = 0

        for round_num in range(1, self.max_rounds + 1):
            self.round_count = round_num
            if self._cancelled:
                logger.info(f"Research cancelled after {round_num - 1} rounds")
                break
            if self._time_exceeded():
                logger.info(f"Time limit reached after {round_num - 1} rounds")
                break

            logger.info(f"=== Research Round {round_num} ===")
            self._emit(phase="searching", round=round_num, total_sources=len(self.urls_fetched))

            # THINK: choose bounded navigation actions, falling back to the
            # legacy query-array path for weak/non-JSON model replies.
            actions = await self._plan_research_actions(question, report, round_num)
            if round_num == 1:
                actions = self._merge_seed_actions(
                    self._explicit_url_actions(question),
                    actions,
                    max_actions=4,
                )
            queries = [a.args.get("query", "") for a in actions if a.tool == "web_search" and a.args.get("query")]
            if not actions:
                queries = await self._generate_queries(question, report, round_num)
                actions = [ResearchAction("web_search", {"query": q}) for q in queries]
                self._record_action_plan(round_num, actions, source="query_fallback")
            if not actions:
                logger.warning(f"Round {round_num}: no queries generated, stopping")
                if not findings:
                    self._set_failure(
                        "query_generation",
                        "Research could not generate search queries. Check the research model and try again.",
                    )
                break

            self._emit(phase="searching", round=round_num, queries=len(queries),
                       query_preview=queries[0] if queries else "",
                       total_sources=len(self.urls_fetched))

            # SEARCH + EXTRACT
            round_findings = await self._execute_research_actions(actions, question)
            if round_findings:
                findings.extend(round_findings)
                self.findings = findings
                consecutive_empty_rounds = 0
                logger.info(f"Round {round_num}: extracted {len(round_findings)} findings")
                self._emit(phase="reading", round=round_num,
                           new_sources=len(round_findings),
                           total_sources=len(self.urls_fetched),
                           total_findings=len(findings),
                           source_state=self._source_state_summary())
            else:
                consecutive_empty_rounds += 1
                logger.info(f"Round {round_num}: no new findings ({consecutive_empty_rounds} consecutive empty)")
                if consecutive_empty_rounds >= self.max_empty_rounds:
                    logger.warning(f"Research gathered no new evidence in {self.max_empty_rounds} consecutive rounds")
                    if not findings:
                        return self._no_evidence_report()
                    break

            # SYNTHESIZE
            if findings:
                if self.fast_fact_mode:
                    report = self._compact_fact_report(question, findings)
                    self.evolving_report = report
                    break
                self._emit(phase="analyzing", round=round_num,
                           total_sources=len(self.urls_fetched),
                           total_findings=len(findings),
                           source_state=self._source_state_summary())
                report = await self._synthesize(question, findings, report)
                self.evolving_report = report

            # DECIDE
            if round_num >= self.min_rounds:
                depth_reason = self._needs_more_evidence_before_stop(round_num)
                if depth_reason:
                    logger.info("Continuing research before stop decision: %s", depth_reason)
                    self._emit(phase="analyzing", round=round_num,
                               message=f"Continuing: {depth_reason}",
                               total_sources=len(self.urls_fetched),
                               total_findings=len(findings),
                               source_state=self._source_state_summary())
                    continue
                should_stop = await self._should_stop(question, report, round_num)
                if should_stop:
                    logger.info(f"LLM decided to stop after round {round_num}")
                    break

        # FINAL REPORT
        self._emit(phase="writing", total_sources=len(self.urls_fetched),
                   total_findings=len(findings))
        if not report:
            # Synthesis can fail (e.g. the LLM timed out) even though the search
            # rounds did gather findings. Don't throw that work away — return the
            # gathered findings as a basic compiled report instead of claiming
            # nothing was found (#1551).
            if findings:
                logger.warning(
                    "Synthesis produced no report; returning %d gathered "
                    "finding(s) as a fallback", len(findings)
                )
                return self._fallback_report(question, findings)
            return self._no_evidence_report()

        self.evolving_report = report  # preserve pre-synthesis report
        if self.fast_fact_mode:
            # The compact factual path is already the final report. Sending it
            # through _final_report would add another slow generation pass on
            # small local models and can make a successful lookup appear to
            # hang or fail.
            logger.info("Research complete via fast factual report")
            return report
        final = await self._final_report(question, report)
        elapsed = time.time() - self._start_time
        logger.info(
            f"Research complete: {self.round_count} rounds, "
            f"{len(findings)} findings, {len(self.urls_fetched)} URLs, "
            f"{elapsed:.1f}s"
        )
        return final

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------
    async def _llm(self, messages: List[Dict], temperature: float = 0.3,
                   max_tokens: int = 4096, timeout: int = 60,
                   enable_thinking: Optional[bool] = None) -> str:
        """Ground every research call in the current date and require final text."""
        from src.llm_core import llm_call_async
        thinking_options = {"enable_thinking": enable_thinking} if enable_thinking is not None else {}
        response = await llm_call_async(
            url=self.llm_endpoint,
            model=self.llm_model,
            messages=[{"role": "system", "content": current_date_context()}] + messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=self.llm_headers,
            timeout=timeout,
            require_complete_response=True,
            **thinking_options,
        )
        return strip_thinking(response)

    # ------------------------------------------------------------------
    # PLAN: create research strategy
    # ------------------------------------------------------------------
    async def _create_plan(self, question: str) -> str:
        """LLM analyzes the question and creates a research plan."""
        prompt = current_date_context() + RESEARCH_PLAN_PROMPT.format(question=question)
        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
                timeout=getattr(self, "planning_timeout", 90),
                enable_thinking=False,
            )
            # Try to parse as JSON for structured plan
            parsed = self._parse_json_object(response)
            if parsed:
                parts = []
                if parsed.get("sub_questions"):
                    parts.append("Sub-questions: " + "; ".join(parsed["sub_questions"]))
                if parsed.get("key_topics"):
                    parts.append("Key topics: " + ", ".join(parsed["key_topics"]))
                if parsed.get("success_criteria"):
                    parts.append("Success: " + parsed["success_criteria"])
                return "\n".join(parts) if parts else response
            return response
        except Exception as e:
            logger.warning(f"Research planning failed: {e}")
            self._emit(phase="warning", message="Planning step failed, proceeding with direct search")
            return ""

    async def _classify_category(self, question: str, research_plan: str = "") -> Optional[str]:
        """Choose the report structure from explicit intent and planning context."""
        inferred = _infer_research_category(question)
        if inferred:
            return inferred
        plan_context = str(research_plan or "").strip()[:1600]
        prompt = (
            "Choose the report STRUCTURE that best answers this research request.\n"
            "Return exactly one label: product, comparison, howto, factcheck, or general.\n\n"
            "product: the reader is choosing what or where to purchase; prices, ranked picks, "
            "pros/cons, and sellers are useful.\n"
            "comparison: two or more options need direct side-by-side criteria and best-for verdicts.\n"
            "howto: the reader wants actionable ordered steps to complete a task.\n"
            "factcheck: the reader asks whether a specific factual claim is true and needs evidence "
            "for, against, and a verdict.\n"
            "general: explanations, current news, history, broad surveys, analyses, or any request "
            "that does not clearly need one specialized structure.\n\n"
            "Do not select product merely because software or a product is mentioned. Do not select "
            "howto for 'how does X work' explanations. Do not select comparison for a report that "
            "only mentions several related things without asking to evaluate them.\n\n"
            f"Question: {question}\n"
            f"Research plan: {plan_context or '(not available)'}\n\n"
            "Label:"
        )
        try:
            result = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0, max_tokens=20, timeout=15,
                enable_thinking=False,
            )
            cat = (result or "").strip().lower()
            # Clean one-word answer first.
            parts = cat.split()
            first = parts[0].strip(".,\"'*:") if parts else ""
            if first in CATEGORY_PROMPTS:
                return first
            # Weak local models often wrap the label in preamble ("the category
            # is product") — scan the whole reply for any known category word
            # before giving up (which would default to the generic format).
            for c in CATEGORY_PROMPTS:
                if c in cat:
                    return c
            return None
        except Exception as e:
            logger.warning(f"Category classification failed: {e}")
            return None

    # ------------------------------------------------------------------
    # THINK: generate search queries
    # ------------------------------------------------------------------
    async def _generate_queries(self, question: str, report: str,
                                round_num: int) -> List[str]:
        if round_num == 1:
            num_queries = 4
            round_instruction = (
                "This is the first round — generate broad, diverse queries "
                "that explore the key facets of the question."
            )
        else:
            num_queries = 3
            round_instruction = (
                "We already have partial findings.  Generate targeted follow-up "
                "queries to fill gaps, verify claims, or explore specific aspects "
                "that the report doesn't yet cover well."
            )

        if getattr(self, "simple_research_mode", False):
            prompt = SMALL_MODEL_QUERY_GEN_PROMPT.format(
                today=datetime.now().astimezone().strftime("%Y-%m-%d"),
                question=question,
                round_num=round_num,
                num_queries=num_queries,
            )
        else:
            prompt = current_date_context() + QUERY_GEN_PROMPT.format(
                question=question,
                research_plan=self.research_plan or "(No plan — search broadly.)",
                report=report or "(No findings yet.)",
                round_num=round_num,
                num_queries=num_queries,
                round_instruction=round_instruction,
            )

        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=512 if getattr(self, "simple_research_mode", False) else 4096,
                timeout=getattr(self, "query_timeout", 120),
                enable_thinking=False,
            )
            queries = self._parse_json_array(response)
            # Deduplicate
            new_queries = [
                q for q in queries
                if q not in self.queries_used and not _is_meta_search_query(q)
            ]
            # A weak/local model can return an empty response or malformed
            # JSON even when the question is perfectly searchable. Never let
            # that silently terminate research with zero sources: the user's
            # question is a valid broad discovery query and gives the next
            # stage a chance to recover.
            if not new_queries:
                fallback = self._deterministic_search_topic(question)
                fallback_queries = [
                    fallback,
                    f"{fallback} fact check",
                    f"{fallback} reliable sources",
                ]
                new_queries = [
                    query for query in fallback_queries
                    if query and not _is_meta_search_query(query)
                    and query not in self.queries_used
                ][:num_queries]
                if new_queries:
                    logger.warning(
                        "Round %s query planner returned no usable queries; "
                        "using deterministic fallback searches: %s",
                        round_num, new_queries,
                    )
                    self._emit(
                        phase="warning",
                        message="Search planning returned no usable queries; trying fallback searches.",
                    )
            self.queries_used.update(new_queries)
            logger.info(f"Round {round_num} queries: {new_queries}")
            return new_queries
        except Exception as e:
            logger.error(f"Query generation failed: {e}")
            self._emit(phase="warning", message=f"Query generation failed: {e}")
            return []

    async def _plan_research_actions(self, question: str, report: str,
                                     round_num: int) -> List[ResearchAction]:
        """Let the model choose bounded search/fetch/browser actions."""
        if getattr(self, "simple_research_mode", False):
            # Small local models are much more reliable at producing a short
            # query list than a nested tool/action protocol. The caller will
            # use _generate_queries instead.
            return []
        try:
            from src.settings import get_setting

            enabled = bool(get_setting("research_action_planning", True))
        except Exception:
            enabled = True
        if not enabled:
            return []

        max_actions = 4 if round_num == 1 else 3
        visited = "\n".join(f"- {u}" for u in list(self.urls_fetched)[-20:]) or "(none)"
        prompt = current_date_context() + RESEARCH_ACTION_PROMPT.format(
            question=question,
            research_plan=self.research_plan or "(No plan yet.)",
            report=report or "(No findings yet.)",
            source_state=self._source_state_summary(),
            source_coverage_json=self._source_coverage_json(),
            navigation_trace=self._navigation_trace_summary(),
            visited_urls=visited,
            round_num=round_num,
            max_actions=max_actions,
        )
        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.25,
                max_tokens=1536,
                timeout=getattr(self, "query_timeout", 120),
                enable_thinking=False,
            )
        except Exception as e:
            logger.warning("Research action planning failed: %s", e)
            self._emit(phase="warning", message=f"Research action planning failed: {e}")
            return []

        actions = parse_research_actions(response)
        rejected_actions: List[Dict[str, object]] = []
        actions = self._normalize_research_actions(
            actions,
            max_actions=max_actions,
            rejected=rejected_actions,
        )
        if actions:
            logger.info("Round %s research actions: %s", round_num, actions)
            self._record_action_plan(round_num, actions, source="planner")
        if rejected_actions:
            self._record_action_rejections(round_num, rejected_actions, source="planner")
        return actions

    def _normalize_research_actions(self, actions: List[ResearchAction], *,
                                    max_actions: int,
                                    rejected: Optional[List[Dict[str, object]]] = None) -> List[ResearchAction]:
        normalized: List[ResearchAction] = []
        seen = set()

        def _reject(tool: str, args: Dict, reason: str) -> None:
            if rejected is not None:
                rejected.append({
                    "tool": str(tool or ""),
                    "query": str(args.get("query") or "")[:240],
                    "url": str(args.get("url") or "")[:500],
                    "reason": reason,
                })

        for action in actions:
            tool = action.tool
            args = dict(action.args or {})
            queries_used = getattr(self, "queries_used", set())
            urls_fetched = getattr(self, "urls_fetched", set())
            original_tool = tool
            original_action = str(args.get("action") or "").strip().lower()
            if tool == "private_browser":
                if original_action in {"", "read", "open", "snapshot", "screenshot"}:
                    tool = "browser_read"
                else:
                    _reject(tool, args, f"unsupported private_browser action: {original_action}")
                    continue
            if tool in {"browser_open", "browser_snapshot"}:
                tool = "browser_read"
            if tool == "web_search":
                query = str(args.get("query") or args.get("q") or args.get("search_query") or "").strip()
                if not query:
                    _reject(tool, args, "empty query")
                    continue
                if query in queries_used:
                    _reject(tool, args, "duplicate query")
                    continue
                if _is_meta_search_query(query):
                    _reject(tool, args, "meta search request")
                    continue
                key = (tool, query.lower())
                args = {"query": query}
            elif tool in {"web_fetch", "browser_read"}:
                url = str(args.get("url") or args.get("href") or args.get("link") or "").strip()
                if not url:
                    _reject(tool, args, "empty URL")
                    continue
                if url in urls_fetched:
                    _reject(tool, args, "already visited URL")
                    continue
                key = (tool, url)
                normalized_args = {"url": url}
                requested_by = str(args.get("requested_by") or "").strip()
                if original_tool != tool and not requested_by:
                    requested_by = (
                        f"{original_tool}.{original_action}"
                        if original_tool == "private_browser" and original_action
                        else original_tool
                    )
                if requested_by:
                    normalized_args["requested_by"] = requested_by
                args = normalized_args
            else:
                _reject(tool, args, "unsupported tool")
                continue
            if key in seen:
                _reject(tool, args, "duplicate action")
                continue
            seen.add(key)
            normalized.append(ResearchAction(tool, args))
            if len(normalized) >= max_actions:
                break
        return normalized

    def _merge_seed_actions(self, seed_actions: List[ResearchAction],
                            planned_actions: List[ResearchAction], *,
                            max_actions: int) -> List[ResearchAction]:
        """Prefer concrete user-provided URLs, then keep model-planned actions."""
        if not seed_actions:
            return planned_actions
        return self._normalize_research_actions(
            [*seed_actions, *(planned_actions or [])],
            max_actions=max_actions,
        )

    def _explicit_url_actions(self, question: str, *, max_urls: int = 4) -> List[ResearchAction]:
        """Turn URLs in the user's research question into direct reads."""
        actions: List[ResearchAction] = []
        seen = set()
        for url in self._extract_explicit_urls(question):
            if url in seen:
                continue
            seen.add(url)
            actions.append(ResearchAction("web_fetch", {
                "url": url,
                "requested_by": "explicit_url",
            }))
            if len(actions) >= max_urls:
                break
        return actions

    @staticmethod
    def _extract_explicit_urls(text: str) -> List[str]:
        urls: List[str] = []
        for raw in re.findall(r'https?://[^\s<>"\']+', text or ""):
            url = raw.rstrip('.,);]}')
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                urls.append(urllib.parse.urlunparse(parsed))
        return urls

    @staticmethod
    def _result_host(url: str) -> str:
        parsed = urllib.parse.urlparse(str(url or ""))
        return (parsed.netloc or parsed.path.split("/", 1)[0]).lower().removeprefix("www.")

    @staticmethod
    def _topic_terms(question: str) -> Set[str]:
        """Return meaningful topic anchors from a research question.

        Search engines frequently return pages that match only a generic word
        such as ``best`` or ``Boston``.  Those pages are especially dangerous
        for small models: the extractor can turn an unrelated page into a
        plausible-looking answer.  Keep this deliberately conservative and
        use the same anchors for search-result and fetched-page gates.
        """
        stopwords = {
            "a", "about", "an", "and", "are", "be", "can", "does", "for",
            "from", "how", "in", "is", "it", "latest", "of", "on", "or",
            "prone", "should", "the", "this", "to", "was", "were", "what",
            "when", "where", "which", "why", "with", "would",
        }
        return {
            token for token in re.findall(r"[^\W_]+", str(question or "").casefold())
            if len(token) >= 2 and token not in stopwords
        }

    @classmethod
    def _topic_overlap(cls, question: str, text: str) -> int:
        """Count distinct question anchors present in text."""
        terms = cls._topic_terms(question)
        haystack = str(text or "").lower()
        overlap = 0
        for term in terms:
            variants = [term]
            if term.endswith("s") and len(term) > 3:
                variants.append(term[:-1])
            if any(re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", haystack)
                   for variant in variants):
                overlap += 1
        return overlap

    @classmethod
    def _topic_relevant(cls, question: str, text: str) -> bool:
        """Require enough topical overlap to let a page reach the model."""
        # This English lexical heuristic cannot decide cross-language
        # relevance or segment unspaced scripts. Defer those to extraction.
        if not str(question or "").isascii() or not str(text or "").isascii():
            return True
        terms = cls._topic_terms(question)
        if not terms:
            return True
        overlap = cls._topic_overlap(question, text)
        # A one-word topic such as "Sweden" is sufficient on its own. For
        # multi-anchor questions, one shared word is not evidence of relevance
        # ("Boston safety" must not qualify for Boston Terrier neurology).
        return overlap >= (1 if len(terms) <= 1 else 2)

    @staticmethod
    def _deterministic_search_topic(question: str) -> str:
        """Turn a failed planner question into a clean search topic."""
        topic = re.sub(r"\s+", " ", str(question or "").strip())
        topic = re.sub(
            r"^(?:please\s+)?(?:what is|what are|where is|where are|who is|"
            r"when was|when is|how does|how do|can you explain)\s+",
            "",
            topic,
            flags=re.IGNORECASE,
        )
        topic = re.sub(r"[?!.,;:]+$", "", topic).strip()
        return topic or re.sub(r"[?!.,;:]+$", "", str(question or "").strip())

    def _prioritize_search_results(self, results: List[Dict], *, limit: int,
                                   question: str = "") -> List[Dict]:
        """Prefer stronger and more diverse search hits before extraction.

        Search providers often rank broad SEO pages above primary sources. This
        keeps the model in charge of query choice while making the bounded
        extraction budget less dependent on provider ordering.
        """
        if limit <= 0:
            return []

        candidates = []
        stopwords = {
            "about", "after", "also", "best", "between", "could", "does",
            "from", "have", "into", "most", "only", "people", "should",
            "still", "that", "their", "there", "these", "this", "what",
            "when", "where", "which", "with", "would", "your", "common",
        }
        definition_question = bool(re.search(
            r"\b(?:define|definition|meaning|mean|what is)\b",
            str(question or "").lower(),
        ))
        question_terms = self._topic_terms(question)
        seen_urls = set()
        for idx, result in enumerate(results or []):
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            if not url or url in seen_urls or url in self.urls_fetched:
                continue
            host = self._result_host(url)
            if not definition_question and any(token in host for token in (
                "dictionary", "wiktionary", "merriam-webster", "collinsdictionary",
            )):
                continue
            seen_urls.add(url)
            title = str(result.get("title") or "")
            summary = str(result.get("content") or result.get("snippet") or "")
            searchable_text = " ".join((title, summary, url)).lower()
            result_terms = set(re.findall(r"[a-z0-9]+", searchable_text))
            relevance = len(question_terms & result_terms)
            assessment = assess_source(url, title=title, summary=summary)
            candidates.append({
                "idx": idx,
                "host": host,
                "assessment": assessment,
                "relevance": relevance,
                "result": result,
            })

        # If the provider returned at least one topic-relevant hit, do not
        # spend extraction slots on generic dictionary/listicle results that
        # only matched a word such as "best". If every hit lacks metadata or
        # overlap, retain the old quality-based behavior rather than returning
        # nothing.
        relevant = [candidate for candidate in candidates if self._topic_relevant(
            question,
            " ".join((candidate["result"].get("title") or "", candidate["result"].get("content") or candidate["result"].get("snippet") or "", candidate["result"].get("url") or "")),
        )]
        if relevant:
            candidates = relevant
        candidates.sort(key=lambda c: (-c["relevance"], -c["assessment"].score, c["host"], c["idx"]))
        picked = []
        picked_ids = set()
        used_hosts = set()

        for candidate in candidates:
            if candidate["host"] in used_hosts:
                continue
            picked.append(candidate)
            picked_ids.add(candidate["idx"])
            used_hosts.add(candidate["host"])
            if len(picked) >= limit:
                break

        if len(picked) < limit:
            for candidate in candidates:
                if candidate["idx"] in picked_ids:
                    continue
                picked.append(candidate)
                if len(picked) >= limit:
                    break

        prioritized = []
        for candidate in picked:
            result = dict(candidate["result"])
            result["_source_kind_hint"] = candidate["assessment"].kind
            result["_source_score_hint"] = candidate["assessment"].score
            result["_source_reason_hint"] = candidate["assessment"].reason
            prioritized.append(result)
        return prioritized

    # ------------------------------------------------------------------
    # SEARCH + EXTRACT
    # ------------------------------------------------------------------
    async def _execute_research_actions(self, actions: List[ResearchAction],
                                        question: str) -> List[Dict]:
        """Execute bounded research actions and extract relevant findings."""
        all_findings: List[Dict] = []
        queries = [str(a.args.get("query") or "").strip() for a in actions if a.tool == "web_search"]
        self.queries_used.update(q for q in queries if q)
        direct_reads = [
            a for a in actions
            if a.tool in {"web_fetch", "browser_read"} and str(a.args.get("url") or "").strip()
        ]

        # Search all queries in parallel
        search_tasks = [self._search(q) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        urls_to_fetch = []
        for action in direct_reads:
            url = str(action.args.get("url") or "").strip()
            if not url or url in self.urls_fetched:
                continue
            assessment = assess_source(url, title=url)
            self.urls_fetched.add(url)
            urls_to_fetch.append({"url": url, "title": url, "_research_action": action.tool})
            self.analyzed_urls.append({
                "url": url,
                "title": url,
                "requested_by": action.args.get("requested_by") or action.tool,
                "source_kind": assessment.kind,
                "source_score": assessment.score,
                "source_reason": assessment.reason,
            })

        # Collect URLs from searches after direct reads so user-provided URLs
        # get extraction capacity first when max URLs per round is tight.
        raw_search_hits = []
        for result in search_results:
            if isinstance(result, Exception):
                logger.warning(f"Search error: {result}")
                continue
            if not result:
                continue
            for r in result:
                if isinstance(r, dict):
                    raw_search_hits.append(r)

        search_limit = self.max_urls_per_round * max(1, len(queries))
        ranked = self._prioritize_search_results(
            raw_search_hits, limit=len(raw_search_hits), question=question
        )
        # Retain the source-quality and topical filters, then share bounded
        # extraction capacity across the planner's different search topics.
        query_urls = [
            {str(r.get("url") or "").strip() for r in result if isinstance(r, dict)}
            if isinstance(result, list) else set()
            for result in search_results
        ]
        buckets = [iter([r for r in ranked if r["url"] in urls]) for urls in query_urls]
        selected, selected_urls = [], set()
        while buckets and len(selected) < search_limit:
            following = []
            for bucket in buckets:
                for result in bucket:
                    if result["url"] not in selected_urls:
                        selected.append(result)
                        selected_urls.add(result["url"])
                        following.append(bucket)
                        break
                if len(selected) >= search_limit:
                    break
            buckets = following
        for r in selected:
            url = str(r.get("url") or "").strip()
            if not url or url in self.urls_fetched:
                continue
            assessment = assess_source(
                url,
                title=str(r.get("title") or ""),
                summary=str(r.get("content") or r.get("snippet") or ""),
            )
            urls_to_fetch.append(r)
            self.urls_fetched.add(url)
            self.analyzed_urls.append({
                "url": url,
                "title": r.get("title", "") or url,
                "requested_by": "web_search",
                "source_kind": assessment.kind,
                "source_score": assessment.score,
                "source_reason": assessment.reason,
            })

        if self._cancelled or self._time_exceeded():
            return all_findings

        # Fetch and extract URLs with backpressure. Local model servers often
        # serialize requests behind one GPU; flooding them makes every request
        # slower and can trip the extraction timeout.
        semaphore = asyncio.Semaphore(self.extraction_concurrency)

        async def _bounded_extract(result: Dict) -> Optional[Dict]:
            async with semaphore:
                mode = result.get("_research_action") or "web_fetch"
                args = (result["url"], question, result.get("title", ""))
                if mode == "browser_read":
                    return await self._fetch_and_extract(*args, prefer_browser=True)
                # Keep the legacy three-argument override contract for normal
                # search/fetch extraction. Existing research extensions often
                # subclass this hook and should not break merely because the
                # engine gained an optional browser-specific path.
                return await self._fetch_and_extract(*args)

        extract_tasks = [_bounded_extract(r) for r in urls_to_fetch]
        results_gathered = await asyncio.gather(*extract_tasks, return_exceptions=True)

        for result in results_gathered:
            if isinstance(result, Exception):
                logger.warning(f"Extraction error: {result}")
                continue
            if result:
                all_findings.append(result)

        return all_findings

    async def _search_and_extract(self, queries: List[str],
                                  question: str) -> List[Dict]:
        """Legacy query-array wrapper retained for compatibility/tests."""
        actions = [ResearchAction("web_search", {"query": q}) for q in queries]
        return await self._execute_research_actions(actions, question)

    async def _search(self, query: str) -> List[Dict]:
        """Run a search query using the configured research search provider."""
        navigator = getattr(self, "navigator", None)
        if navigator is None:
            navigator = ResearchNavigator(
                search_provider=self.search_provider_override,
                session_id=getattr(self, "session_id", ""),
            )
            self.navigator = navigator
        try:
            results = await navigator.search(query, count=10)
            self.providers_used = list(dict.fromkeys([*self.providers_used, *navigator.providers_used]))
            status = "ok" if results else "no_results"
            error = ""
            if not results and navigator.last_search_error:
                self._last_search_error = navigator.last_search_error
                error = navigator.last_search_error
                self._remember_search_error(error)
            self._record_navigation(
                "web_search",
                query=query,
                status=status,
                results=len(results or []),
                error=error,
            )
            return results
        except Exception as e:
            self._last_search_error = str(e)
            self._remember_search_error(self._last_search_error)
            self._record_navigation("web_search", query=query, status="error", error=str(e))
            raise

    async def _fetch_and_extract(self, url: str, question: str,
                                 title: str, *, prefer_browser: bool = False) -> Optional[Dict]:
        """Fetch a URL's content and use LLM to extract relevant info."""
        display = title or url
        self._emit(phase="reading", url=url, title=display,
                   total_sources=len(self.urls_fetched))
        navigator = getattr(self, "navigator", None)
        if navigator is None:
            navigator = ResearchNavigator(
                progress_callback=self._progress,
                search_provider=self.search_provider_override,
                session_id=getattr(self, "session_id", ""),
            )
            self.navigator = navigator

        page: ResearchPage
        requested_tool = "browser_read" if prefer_browser else "web_fetch"
        if prefer_browser:
            page = await navigator.browser_read(url)
        else:
            page = await navigator.fetch(url, timeout=10)
        if not page.success or not page.content:
            self._record_navigation(
                requested_tool,
                url=url,
                title=display,
                status="no_content" if page.success else "error",
                retrieval=page.retrieval,
                error=getattr(page, "error", "") or "",
            )
            browser_page = await self._browser_fallback(url, title, page)
            if browser_page and browser_page.success and browser_page.content:
                page = browser_page
            else:
                return None

        # Do this before asking the LLM to extract anything. A weak local
        # model may confidently answer the goal from an unrelated page even
        # when the page itself says it contains no relevant information.
        page_topic_text = " ".join((page.title or title or "", page.content or "", url))
        if (getattr(self, "simple_research_mode", False)
                and not self._topic_relevant(question, page_topic_text)
                and page.retrieval != "browser"):
            browser_page = await self._browser_fallback(url, title, page)
            if browser_page and browser_page.success and browser_page.content:
                page = browser_page
                page_topic_text = " ".join((page.title or title or "", page.content or "", url))
        if (getattr(self, "simple_research_mode", False)
                and not self._topic_relevant(question, page_topic_text)):
            logger.info("Skipping topically unrelated research page %s", url)
            self._record_navigation(
                "browser_read" if page.retrieval == "browser" else requested_tool,
                url=url,
                title=title or page.title,
                status="topic_mismatch",
                retrieval=page.retrieval,
            )
            return None

        tried_browser_after_weak_extract = False
        while True:
            content = page.content
            # Truncate to avoid blowing up context, preferring paragraph boundary
            if len(content) > self.max_content_chars:
                truncated = content[:self.max_content_chars]
                last_para = truncated.rfind('\n\n')
                if last_para > self.max_content_chars * 0.8:
                    content = truncated[:last_para]
                else:
                    content = truncated

            try:
                response = await self._llm(
                    [
                        {"role": "user", "content": RESEARCH_EXTRACTOR_PROMPT.format(goal=question)},
                        untrusted_context_message("webpage", content),
                    ],
                    temperature=0.2,
                    max_tokens=2048,
                    timeout=self.extraction_timeout,
                    enable_thinking=False,
                )
            except Exception as e:
                logger.warning(f"LLM extraction failed for {url}: {e}")
                self._record_navigation(
                    "browser_read" if page.retrieval == "browser" else requested_tool,
                    url=url,
                    title=title or page.title,
                    status="extraction_error",
                    retrieval=page.retrieval,
                    error=str(e),
                )
                return None

            parsed = self._parse_json_object(response)
            if parsed is not None:
                if (not isinstance(parsed, dict)
                        or ("relevant" in parsed and not isinstance(parsed["relevant"], bool))
                        or parsed.get("relevant") is False):
                    logger.info(f"Skipping irrelevant extraction from {url}")
                    self._record_navigation(requested_tool, url=url, title=title or page.title,
                                            status="irrelevant", retrieval=page.retrieval)
                    return None
                summary = parsed.get("summary", "")
                evidence = parsed.get("evidence", "")
                if (not isinstance(summary, str) or not summary.strip()
                        or not isinstance(evidence, str) or not evidence.strip()
                        or _unusable_research_text(summary)):
                    logger.info(f"Skipping empty or low-quality extraction from {url}")
                    self._record_navigation(requested_tool, url=url, title=title or page.title,
                                            status="low_quality", retrieval=page.retrieval)
                    if page.retrieval != "browser" and not tried_browser_after_weak_extract:
                        tried_browser_after_weak_extract = True
                        browser_page = await self._browser_fallback(url, title, page)
                        if browser_page and browser_page.success and browser_page.content:
                            page = browser_page
                            continue
                    return None
                parsed["url"] = url
                parsed["title"] = title or page.title
                parsed["og_image"] = page.og_image
                parsed["retrieval"] = page.retrieval
                assessment = assess_source(
                    url,
                    title=parsed["title"],
                    retrieval=page.retrieval,
                    summary=str(parsed.get("summary") or parsed.get("evidence") or ""),
                )
                parsed["source_kind"] = assessment.kind
                parsed["source_score"] = assessment.score
                parsed["source_reason"] = assessment.reason
                if page.retrieval == "browser":
                    self._mark_analyzed_url(url, retrieval="browser")
                self._mark_analyzed_url(
                    url,
                    source_kind=assessment.kind,
                    source_score=assessment.score,
                    source_reason=assessment.reason,
                )
                self._record_navigation(
                    "browser_read" if page.retrieval == "browser" else requested_tool,
                    url=url,
                    title=parsed["title"],
                    status="ok",
                    retrieval=page.retrieval,
                    source_kind=assessment.kind,
                    source_score=assessment.score,
                )
                return parsed
            # If JSON parsing fails, treat entire response as evidence
            if not response.strip() or _unusable_research_text(response):
                self._record_navigation(requested_tool, url=url, title=title or page.title,
                                        status="low_quality", retrieval=page.retrieval)
                return None
            assessment = assess_source(
                url,
                title=title or page.title,
                retrieval=page.retrieval,
                summary=response,
            )
            self._record_navigation(
                "browser_read" if page.retrieval == "browser" else requested_tool,
                url=url,
                title=title or page.title,
                status="ok",
                retrieval=page.retrieval,
                source_kind=assessment.kind,
                source_score=assessment.score,
            )
            self._mark_analyzed_url(
                url,
                source_kind=assessment.kind,
                source_score=assessment.score,
                source_reason=assessment.reason,
            )
            return {
                "url": url,
                "title": title or page.title,
                "og_image": page.og_image,
                "retrieval": page.retrieval,
                "source_kind": assessment.kind,
                "source_score": assessment.score,
                "source_reason": assessment.reason,
                "rational": "LLM extraction (raw)",
                "evidence": response[:3000],
                "summary": response[:500],
            }

    async def _browser_fallback(self, url: str, title: str, page) -> Optional[object]:
        """Use the private browser when a normal text fetch cannot read a page."""
        try:
            from src.settings import get_setting

            enabled = bool(get_setting("research_browser_fallback", True))
        except Exception:
            enabled = True
        if not enabled:
            return None
        navigator = getattr(self, "navigator", None)
        if navigator is None:
            return None
        reason = getattr(page, "error", "") or "no readable text content"
        logger.info("Research browser fallback for %s: %s", url, reason)
        self._emit(phase="navigating", url=url, title=title or url, message="Opening page in private browser")
        try:
            browser_page = await navigator.browser_read(url)
            if not getattr(browser_page, "success", False) or not getattr(browser_page, "content", ""):
                self._record_navigation(
                    "browser_read",
                    url=url,
                    title=title or url,
                    status="no_content" if getattr(browser_page, "success", False) else "error",
                    retrieval="browser",
                    error=getattr(browser_page, "error", "") or "browser returned no readable content",
                )
            return browser_page
        except Exception as e:
            logger.warning("Research browser fallback failed for %s: %s", url, e)
            self._record_navigation("browser_read", url=url, title=title or url,
                                    status="error", retrieval="browser", error=str(e))
            return None

    def _mark_analyzed_url(self, url: str, **updates) -> None:
        for item in self.analyzed_urls:
            if item.get("url") == url:
                item.update({k: v for k, v in updates.items() if v})
                return

    def _record_navigation(self, tool: str, **fields) -> None:
        """Keep a bounded trace of research navigation outcomes."""
        trace = getattr(self, "navigation_trace", None)
        if trace is None:
            trace = []
            self.navigation_trace = trace
        item = {
            "tool": str(tool or ""),
            "status": str(fields.get("status") or ""),
            "query": str(fields.get("query") or "")[:240],
            "url": str(fields.get("url") or "")[:500],
            "title": str(fields.get("title") or "")[:240],
            "retrieval": str(fields.get("retrieval") or ""),
            "error": str(fields.get("error") or "")[:240],
        }
        for key in ("results", "source_score"):
            try:
                value = int(fields.get(key))
                item[key] = value
            except (TypeError, ValueError):
                pass
        source_kind = str(fields.get("source_kind") or "")
        if source_kind:
            item["source_kind"] = source_kind
        trace.append({k: v for k, v in item.items() if v != ""})
        del trace[:-80]

    def _record_action_plan(self, round_num: int, actions: List[ResearchAction],
                            *, source: str = "planner") -> None:
        """Keep a bounded trace of model-planned research actions."""
        trace = getattr(self, "action_trace", None)
        if trace is None:
            trace = []
            self.action_trace = trace
        for action in actions or []:
            if not isinstance(action, ResearchAction):
                continue
            args = dict(action.args or {})
            item = {
                "round": int(round_num or 0),
                "source": str(source or "planner"),
                "tool": str(action.tool or ""),
                "query": str(args.get("query") or "")[:240],
                "url": str(args.get("url") or "")[:500],
                "requested_by": str(args.get("requested_by") or "")[:120],
            }
            trace.append({k: v for k, v in item.items() if v not in ("", 0)})
        del trace[:-80]

    def _record_action_rejections(self, round_num: int, rejected: List[Dict[str, object]],
                                  *, source: str = "planner") -> None:
        trace = getattr(self, "action_trace", None)
        if trace is None:
            trace = []
            self.action_trace = trace
        for item in rejected or []:
            if not isinstance(item, dict):
                continue
            trace.append({
                k: v for k, v in {
                    "round": int(round_num or 0),
                    "source": str(source or "planner"),
                    "status": "skipped",
                    "tool": str(item.get("tool") or ""),
                    "query": str(item.get("query") or "")[:240],
                    "url": str(item.get("url") or "")[:500],
                    "reason": str(item.get("reason") or "")[:160],
                }.items() if v not in ("", 0)
            })
        del trace[:-80]

    def _navigation_trace_summary(self, limit: int = 8) -> str:
        """Compact recent tool outcomes for the next planning round."""
        trace = [t for t in getattr(self, "navigation_trace", []) if isinstance(t, dict)]
        if not trace:
            return "(none yet)"
        lines = []
        for item in trace[-limit:]:
            tool = str(item.get("tool") or "tool")
            status = str(item.get("status") or "unknown")
            if item.get("query"):
                target = f'"{item.get("query")}"'
            else:
                target = str(item.get("url") or item.get("title") or "").strip()
            detail = ""
            if "results" in item:
                detail = f"; {item['results']} result(s)"
            elif item.get("source_kind") or item.get("source_score") is not None:
                kind = item.get("source_kind") or "source"
                score = item.get("source_score")
                detail = f"; {kind}"
                if score is not None:
                    detail += f" score {score}"
            elif item.get("error"):
                detail = f"; {item['error']}"
            lines.append(f"- {tool} {target} -> {status}{detail}")
        return "\n".join(lines)

    def _source_state_summary(self) -> str:
        """Compact source-quality state for planning and stop decisions."""
        coverage = self._source_coverage()
        if not coverage["useful_findings"] and not coverage["sources_analyzed"]:
            return (
                "No sources gathered yet. Need discovery searches, then primary/official "
                "sources where available."
            )

        mix = coverage.get("source_mix", {})
        parts = [
            f"Sources analyzed: {coverage['sources_analyzed']}; useful findings: {coverage['useful_findings']}.",
            "Source mix: " + (", ".join(f"{k}={v}" for k, v in sorted(mix.items())) or "unknown"),
            f"Best source score: {coverage['best_source_score']}/100.",
            f"Browser-read pages: {coverage['browser_reads']}.",
        ]
        gaps = coverage.get("gaps") or ["no obvious source-quality gap"]
        parts.append("Gaps: " + "; ".join(gaps) + ".")
        return "\n".join(parts)

    def _source_coverage(self) -> Dict[str, object]:
        """Machine-readable source quality/coverage state."""
        findings = [f for f in getattr(self, "findings", []) if isinstance(f, dict)]
        analyzed = [u for u in getattr(self, "analyzed_urls", []) if isinstance(u, dict)]
        counts: Dict[str, int] = {}
        browser_reads = 0
        scored: List[int] = []
        by_url: Dict[str, Dict] = {}
        anonymous: List[Dict] = []
        for item in [*analyzed, *findings]:
            url = str(item.get("url") or "")
            if url:
                merged = dict(by_url.get(url) or {})
                for key, value in item.items():
                    if value not in (None, "", [], {}):
                        merged[key] = value
                by_url[url] = merged
            else:
                anonymous.append(item)
        for item in [*by_url.values(), *anonymous]:
            url = str(item.get("url") or "")
            kind = str(item.get("source_kind") or "").strip()
            if not kind and url:
                assessment = assess_source(
                    url,
                    title=str(item.get("title") or ""),
                    retrieval=str(item.get("retrieval") or ""),
                    summary=str(item.get("summary") or item.get("evidence") or ""),
                )
                kind = assessment.kind
                scored.append(assessment.score)
            else:
                try:
                    scored.append(int(item.get("source_score")))
                except (TypeError, ValueError):
                    pass
            if kind:
                counts[kind] = counts.get(kind, 0) + 1
            if str(item.get("retrieval") or "").lower() == "browser":
                browser_reads += 1

        best = max(scored) if scored else 0
        gaps = []
        primary_count = counts.get("official", 0) + counts.get("primary", 0)
        if primary_count == 0:
            gaps.append("primary/official evidence missing")
        if len(findings) < 3:
            gaps.append("source diversity still thin")
        if counts.get("commercial", 0) and primary_count == 0:
            gaps.append("commercial/listicle evidence needs verification")
        if not gaps:
            gaps.append("no obvious source-quality gap")
        return {
            "sources_analyzed": len(analyzed),
            "useful_findings": len(findings),
            "unique_urls": len(by_url),
            "source_mix": counts,
            "primary_or_official": primary_count,
            "browser_reads": browser_reads,
            "best_source_score": best,
            "gaps": gaps,
        }

    def _source_coverage_json(self) -> str:
        try:
            return json.dumps(self._source_coverage(), sort_keys=True)
        except Exception:
            return "{}"

    def _needs_more_evidence_before_stop(self, round_num: int) -> str:
        """General depth gate before asking the LLM if research is complete."""
        if round_num >= self.max_rounds:
            return ""
        coverage = self._source_coverage()
        useful = int(coverage.get("useful_findings") or 0)
        analyzed = int(coverage.get("sources_analyzed") or 0)
        primary = int(coverage.get("primary_or_official") or 0)
        best = int(coverage.get("best_source_score") or 0)
        if useful < 2:
            return "fewer than two useful findings"
        if analyzed < 2:
            return "fewer than two analyzed sources"
        if primary == 0 and useful < 4:
            return "no primary or official source yet"
        if best < 55 and useful < 4:
            return "source quality still weak"
        return ""

    # ------------------------------------------------------------------
    # SYNTHESIZE
    # ------------------------------------------------------------------
    async def _synthesize(self, question: str, findings: List[Dict],
                          current_report: str) -> str:
        """LLM synthesizes all findings into an updated report."""
        # Format findings for the prompt
        window = findings[-self.synthesis_window:]
        if len(findings) > self.synthesis_window:
            logger.info(f"Synthesis using last {self.synthesis_window} of {len(findings)} findings")
        findings_text = self._format_findings(window)

        prompt = SYNTHESIZE_PROMPT.format(
            question=question,
            report=current_report or "(First round — no report yet.)",
            new_findings=findings_text,
            source_state=self._source_state_summary(),
            source_coverage_json=self._source_coverage_json(),
            navigation_trace=self._navigation_trace_summary(),
        )

        try:
            return await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.max_report_tokens,
                # Synthesis is a heavy generation call like the final report
                # (which gets 180s); a slow local model (e.g. a 20B served from
                # LM Studio) routinely needs >60s for it. The old 60s cap timed
                # out mid-stream and discarded the round's findings (#1551).
                timeout=180,
            )
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            self._emit(phase="warning", message="Synthesis failed, keeping previous report")
            return current_report  # keep the old report on failure

    # ------------------------------------------------------------------
    # DECIDE
    # ------------------------------------------------------------------
    async def _should_stop(self, question: str, report: str,
                           round_num: int) -> bool:
        """Let the LLM decide whether the report is comprehensive enough."""
        prompt = current_date_context() + STOP_PROMPT.format(
            question=question,
            report=report,
            source_state=self._source_state_summary(),
            source_coverage_json=self._source_coverage_json(),
            round_num=round_num,
            max_rounds=self.max_rounds,
        )

        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=128,
                enable_thinking=False,
            )
            # Reasoning models prepend a <think>...</think> block — strip it
            # before checking for YES/NO, otherwise the answer always looks
            # like it starts with "<THINK>" and the engine never stops.
            clean = strip_thinking(response).strip()
            # Tolerate "**YES**", "Yes.", quotes, etc.
            answer = re.sub(r'^[\s*_`"\'>#\-]+', '', clean).upper()
            should_stop = answer.startswith("YES")
            logger.info(f"Stop decision (round {round_num}): {clean[:120]}")
            return should_stop
        except Exception as e:
            logger.warning(f"Stop decision failed: {e}")
            return False  # continue on error

    # ------------------------------------------------------------------
    # FINAL REPORT
    # ------------------------------------------------------------------
    async def _final_report(self, question: str, report: str) -> str:
        """LLM writes a polished final report, retrying if too short."""
        cat_extra = CATEGORY_PROMPTS.get(self.category or "", "")
        prompt = FINAL_REPORT_PROMPT.format(
            question=question,
            report=report,
            source_state=self._source_state_summary(),
            source_coverage_json=self._source_coverage_json(),
            navigation_trace=self._navigation_trace_summary(limit=12),
        )
        if cat_extra:
            prompt += "\n\n" + cat_extra

        try:
            result = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.max_report_tokens,
                timeout=180,
            )

            # If report is too short, ask the LLM to expand it
            if len(result.split()) < 400:
                logger.info(f"Final report too short ({len(result.split())} words), requesting expansion")
                self._emit(phase="writing", message="Expanding report...")
                expanded = await self._llm(
                    [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": result},
                        {"role": "user", "content":
                            "This report is too brief. Please expand it significantly:\n"
                            "- Add detailed paragraphs for each section (not just bullet points)\n"
                            "- Include specific data, numbers, and comparisons from the evidence\n"
                            "- Explain context and significance — don't just list facts\n"
                            "- Use ## headings and ### subheadings\n"
                            "- Ground every added claim in the collected evidence; do not invent requirements, estimates, or recommendations to reach a word count\n"
                            "- Clearly label unknowns and inferences\n"
                            "- Target at least 1000 words only if the evidence supports that detail\n"
                            "Write the full expanded report now."
                        },
                    ],
                    temperature=0.4,
                    max_tokens=self.max_report_tokens,
                    timeout=180,
                )
                if len(expanded.split()) > len(result.split()):
                    return expanded

            return result
        except Exception as e:
            logger.error(f"Final report generation failed: {e}")
            return report  # return the evolving report as-is

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _no_evidence_report(self) -> str:
        """Preserve the failure stage at any exit without gathered evidence."""
        if self.failure_message:
            return f"**Research failed** — {self.failure_message}"
        if self.urls_fetched:
            self._set_failure(
                "extraction",
                "Research found pages, but none could be read or produced useful evidence. "
                "Check page access and the research model output.",
            )
            return f"**No usable evidence** — {self.failure_message}"
        errors = self._search_errors or [getattr(self, "_last_search_error", "")]
        detail = "; ".join(error for error in errors if error)[:1200]
        rounds = self.round_count
        message = f"Web search returned no pages after {rounds} {'round' if rounds == 1 else 'rounds'}."
        if detail:
            message += " " + detail
        self._set_failure("search", message)
        return (
            f"**Search unavailable** — {self.failure_message}\n\n"
            "Check the search provider settings or try a working search engine."
        )

    def _remember_search_error(self, detail: str):
        errors = getattr(self, "_search_errors", None)
        if errors is None:
            errors = self._search_errors = []
        if detail and detail not in errors and len(errors) < 8:
            errors.append(detail[:1200])

    def _set_failure(self, stage: str, message: str):
        self.failure_stage = stage
        self.failure_message = message[:1600]
        self._emit(
            phase="error", message=self.failure_message,
            failure_stage=self.failure_stage, failure_message=self.failure_message,
        )

    def _emit(self, **kwargs):
        """Send a progress event via the callback, if one is registered."""
        if self._progress:
            try:
                self._progress(kwargs)
            except Exception:
                pass

    def _time_exceeded(self) -> bool:
        return (time.time() - self._start_time) > self.max_time

    # _strip_think_tags removed — use research_utils.strip_thinking()

    @staticmethod
    def _strip_code_block(text: str) -> str:
        """Strip markdown code-block fences (```json ... ```) if present."""
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
        return text.strip()

    def _parse_json_array(self, text: str) -> List[str]:
        """Extract a JSON array of strings from LLM output."""
        text = self._strip_code_block(text)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass

        # Handle truncated arrays — e.g. '["query one", "query two", "query thr'
        # Repair from the LAST array start so an echoed example array earlier
        # in the reply is not harvested into the real query set.
        last_start = text.rfind('[')
        truncated = last_start != -1 and ']' not in text[last_start:]
        if truncated:
            complete_items = re.findall(r'"([^"]*)"', text[last_start:])
            if complete_items:
                logger.info(f"Repaired truncated JSON array: recovered {len(complete_items)} items")
                return complete_items

        # Greedy match to capture the full outermost array
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                pass

        # Multiple complete arrays in one reply (e.g. the model echoes the
        # prompt's Example: [...] before the real array). The greedy match
        # above spans them all and fails to parse, so scan non-greedily and
        # keep the LAST parseable array, which is the model's actual answer.
        last_parsed = None
        for m in re.finditer(r'\[[\s\S]*?\]', text):
            try:
                parsed = json.loads(m.group())
                if isinstance(parsed, list):
                    last_parsed = parsed
            except json.JSONDecodeError:
                continue
        if last_parsed is not None:
            return [str(item) for item in last_parsed]

        # Last resort: harvest quoted strings from the first array start
        arr_start = text.find('[')
        if arr_start != -1:
            fragment = text[arr_start:]
            # Find the last complete quoted string
            complete_items = re.findall(r'"([^"]*)"', fragment)
            if complete_items:
                logger.info(f"Repaired truncated JSON array: recovered {len(complete_items)} items")
                return complete_items

        logger.warning(f"Could not parse JSON array from: {text[:200]}")
        return []

    def _parse_json_object(self, text: str) -> Optional[Dict]:
        """Extract a JSON object from LLM output."""
        text = self._strip_code_block(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Greedy match to capture the full outermost object
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None

    def _format_findings(self, findings: List[Dict]) -> str:
        """Format findings list into readable text for synthesis prompt."""
        parts = []
        for i, f in enumerate(findings, 1):
            url = f.get("url", "unknown")
            title = f.get("title", "")
            summary = f.get("summary", "")
            evidence = f.get("evidence", "")
            # Use summary if available, fall back to truncated evidence
            content = summary if summary else (evidence[:1000] if evidence else "(no content)")
            parts.append(f"**Finding {i}** — [{title}]({url})\n{content}")
        return "\n\n".join(parts)

    def _fallback_report(self, question: str, findings: List[Dict]) -> str:
        """Compile gathered findings into a basic report.

        Used when the LLM synthesis step produced no report (e.g. it timed out)
        but the search rounds did collect findings — so the user still gets the
        material that was gathered instead of "No information could be gathered"
        (#1551).
        """
        return (
            f"# {question}\n\n"
            "_Automatic synthesis did not complete, so this report lists the "
            f"{len(findings)} finding(s) gathered during research._\n\n"
            f"{self._format_findings(findings)}"
        )

    def _compact_fact_report(self, question: str, findings: List[Dict]) -> str:
        """Build a useful answer without a second slow local-model pass."""
        rows = []
        for finding in findings[:4]:
            title = finding.get("title") or finding.get("url") or "Source"
            summary = finding.get("summary") or finding.get("evidence") or ""
            url = finding.get("url") or ""
            if summary:
                rows.append(f"- **{title}**: {summary.strip()} [{url}]({url})")
        return f"## {question.strip()}\n\n" + "\n\n".join(rows)

    def get_stats(self) -> Dict:
        """Return research statistics."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        stats = {
            "Duration": f"{elapsed:.1f}s",
            "Rounds": self.round_count,
            "Queries": len(self.queries_used),
            "URLs": len(self.urls_fetched),
            "Model": self.llm_model,
        }
        if self.providers_used:
            stats["Search"] = ", ".join(self.providers_used)
        navigator = getattr(self, "navigator", None)
        browser_fetches = int(getattr(navigator, "browser_fetches", 0) or 0)
        if browser_fetches:
            stats["Browser reads"] = browser_fetches
        if self.category:
            stats["Category"] = self.category.capitalize()
        return stats
