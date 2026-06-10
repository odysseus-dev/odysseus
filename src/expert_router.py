"""Multi-agent expert orchestrator.

Routes each user question to the single most suitable specialist persona
before the main model answers. A small classification call (the
"orchestrator") reads the message and returns the chosen expert id as JSON; a
deterministic keyword fallback is used when no JSON can be parsed or no model
is reachable, so routing never hard-fails.

This mirrors the existing ``presets`` mechanism (each expert is just a
system prompt + sampling params) and is wired into ``build_chat_context``:
when the user selects the ``experts`` router preset, the chosen specialist's
preset is swapped in for the actual answer.

All experts are strictly defensive/constructive. There is intentionally no
offensive-security or "uncensored" persona.
"""

import json
import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Preset id that activates the orchestrator in the chat flow / picker.
ROUTER_PRESET_ID = "experts"

# Each expert is a self-contained persona: a system prompt plus sampling
# params, exactly like a built-in preset. ``routing_hint`` is shown only to
# the classifier, ``keywords`` drive the offline fallback.
EXPERTS: Dict[str, Dict] = {
    "programmer": {
        "name": "Programmer",
        "temperature": 0.2,
        "max_tokens": 8000,
        "routing_hint": (
            "writing, debugging, refactoring, reviewing or explaining code; "
            "build/compile errors, algorithms, APIs, databases, devops"
        ),
        "keywords": [
            "code", "bug", "debug", "function", "compile", "error", "stack trace",
            "python", "javascript", "typescript", "java", "c++", "rust", "go",
            "api", "sql", "regex", "docker", "git", "refactor", "unit test",
            "exception", "syntax", "algorithm", "framework", "library",
        ],
        "system_prompt": """You are the Programmer specialist of a multi-expert assistant.

Reason step by step (Thought -> Action -> Observation) before the final answer.
For non-trivial problems, break the task into logical steps and show enough of
your reasoning to justify the solution.

Guidelines:
- Give correct, runnable code with all imports and dependencies.
- Prefer minimal, idiomatic solutions over broad rewrites.
- Point out edge cases, complexity, and how to test the result.
- If the request is ambiguous, state your assumptions briefly, then proceed.

Begin your reply with a single line: "Expert: Programmer".
Respond in the same language the user wrote in.""",
    },
    "ai_ml": {
        "name": "AI / ML",
        "temperature": 0.3,
        "max_tokens": 6000,
        "routing_hint": (
            "machine learning, deep learning, LLMs, training, fine-tuning, RAG, "
            "embeddings, transformers, model evaluation, data science theory"
        ),
        "keywords": [
            "machine learning", "deep learning", "neural network", "llm",
            "transformer", "fine-tune", "fine tuning", "training", "dataset",
            "embedding", "rag", "vector", "gradient", "pytorch", "tensorflow",
            "hugging face", "model", "inference", "quantization", "diffusion",
            "reinforcement learning", "overfitting", "hyperparameter",
        ],
        "system_prompt": """You are the AI / ML specialist of a multi-expert assistant.

Reason step by step (Thought -> Action -> Observation) before the final answer.
Break complex problems into logical steps and justify your conclusions.

Guidelines:
- Cover both the theory and the practical implementation when relevant.
- Be precise about trade-offs (accuracy, latency, memory, data needs).
- Recommend concrete tools, architectures, and evaluation methods.
- Distinguish established results from speculation; quantify uncertainty.

Begin your reply with a single line: "Expert: AI / ML".
Respond in the same language the user wrote in.""",
    },
    "security_defensive": {
        "name": "Defensive Security",
        "temperature": 0.2,
        "max_tokens": 6000,
        "routing_hint": (
            "DEFENSIVE security only: hardening, secure coding, threat modeling, "
            "detection, incident response, reviewing code for vulnerabilities"
        ),
        "keywords": [
            "security", "vulnerability", "hardening", "threat model", "owasp",
            "encryption", "authentication", "authorization", "firewall",
            "secure", "csrf", "xss", "injection", "audit", "incident response",
            "malware analysis", "patch", "cve", "least privilege", "tls",
        ],
        "system_prompt": """You are the Defensive Security specialist of a multi-expert assistant.

Reason step by step (Thought -> Action -> Observation) before the final answer.

Scope and ethics (non-negotiable):
- You help ONLY with defensive, protective, and educational security work:
  hardening, secure coding, threat modeling, detection, incident response,
  and reviewing code or configs for weaknesses so they can be fixed.
- You do NOT provide working exploits, malware, credential-cracking, intrusion
  steps, or any guidance whose primary purpose is to attack systems you are not
  authorized to defend. If asked for that, decline and offer a defensive
  alternative instead.

Guidelines:
- Be concrete about mitigations, configurations, and safer patterns.
- Reference recognized standards (OWASP, CIS, NIST) where useful.

Begin your reply with a single line: "Expert: Defensive Security".
Respond in the same language the user wrote in.""",
    },
    "general": {
        "name": "General",
        "temperature": 0.7,
        "max_tokens": 4096,
        "routing_hint": "anything that does not clearly fit another specialist",
        "keywords": [],
        "system_prompt": """You are the General specialist of a multi-expert assistant.

For complex questions, reason step by step before giving the final answer.
Be clear, accurate, and concise. If another domain expert (programming,
AI/ML, defensive security) would clearly serve the user better, say so briefly.

Begin your reply with a single line: "Expert: General".
Respond in the same language the user wrote in.""",
    },
}

DEFAULT_EXPERT = "general"


def _classifier_system_prompt() -> str:
    labels = "\n".join(
        f'- {eid}: {meta["routing_hint"]}' for eid, meta in EXPERTS.items()
    )
    return (
        "You are an orchestrator that routes a user request to exactly one "
        "specialist agent.\n"
        "Specialists:\n"
        f"{labels}\n\n"
        "Pick the single best specialist for the user's message. If none "
        f'clearly fits, choose "{DEFAULT_EXPERT}".\n'
        'Respond with ONLY a compact JSON object and nothing else, in the form: '
        '{"expert": "<id>", "reason": "<short reason>"}.'
    )


def parse_expert_decision(raw: str) -> Tuple[Optional[str], str]:
    """Parse the classifier output into ``(expert_id, reason)``.

    Tolerates code fences and surrounding prose by extracting the first JSON
    object. Returns ``(None, reason)`` when no valid expert can be read, so the
    caller can fall back deterministically.
    """
    if not raw or not str(raw).strip():
        return None, "empty classifier output"
    text = str(raw).strip()
    # Strip a leading ```json / ``` fence if present.
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None, "no JSON object found"
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None, "invalid JSON"
    if not isinstance(data, dict):
        return None, "JSON is not an object"
    expert = data.get("expert")
    reason = str(data.get("reason") or "").strip() or "classified"
    if isinstance(expert, str) and expert.strip() in EXPERTS:
        return expert.strip(), reason
    return None, "unknown expert id"


def keyword_fallback_expert(message: str) -> str:
    """Deterministic offline routing used when classification is unavailable.

    Scores each expert by counting keyword hits in the message and returns the
    best match, defaulting to :data:`DEFAULT_EXPERT` on a tie or no hit.
    """
    text = (message or "").lower()
    best = DEFAULT_EXPERT
    best_score = 0
    for eid, meta in EXPERTS.items():
        # Word-boundary match so short keywords ("go", "api") don't fire on
        # substrings of unrelated words ("good", "rapid").
        score = sum(
            1
            for kw in meta["keywords"]
            if re.search(r"\b" + re.escape(kw) + r"\b", text)
        )
        if score > best_score:
            best_score = score
            best = eid
    return best


async def classify_expert(
    message: str,
    endpoint_url: str,
    model: str,
    headers: Optional[Dict] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Return ``(expert_id, reason)`` for ``message``.

    Tries a small JSON classification call against the session's own model
    (keeping everything local), then falls back to keyword matching.
    """
    if not message or not str(message).strip():
        return DEFAULT_EXPERT, "empty message"

    try:
        from src.llm_core import llm_call_async

        raw = await llm_call_async(
            endpoint_url,
            model,
            [
                {"role": "system", "content": _classifier_system_prompt()},
                {"role": "user", "content": str(message)[:2000]},
            ],
            temperature=0.0,
            max_tokens=120,
            session_id=session_id,
        )
        expert, reason = parse_expert_decision(raw)
        if expert:
            logger.info("expert router -> %s (%s)", expert, reason)
            return expert, reason
        logger.info("expert router: unparseable decision, using keyword fallback")
    except Exception as e:  # network/model errors must not break chat
        logger.warning("expert routing LLM failed (%s); using keyword fallback", e)

    expert = keyword_fallback_expert(message)
    return expert, "keyword fallback"


def expert_presets_for_picker() -> Dict[str, Dict]:
    """Preset entries to register so experts + the router show in the picker.

    The router entry (:data:`ROUTER_PRESET_ID`) is what users select to enable
    automatic routing; the per-expert entries let users also pin one expert
    directly. Shapes match ``PresetManager.DEFAULT_PRESETS``.
    """
    presets: Dict[str, Dict] = {
        ROUTER_PRESET_ID: {
            "name": "Experts (Auto)",
            "temperature": 0.5,
            "max_tokens": 6000,
            "system_prompt": (
                "You are a multi-expert assistant. Answer helpfully and "
                "reason step by step for complex questions."
            ),
        }
    }
    for eid, meta in EXPERTS.items():
        presets[f"expert_{eid}"] = {
            "name": f"Expert: {meta['name']}",
            "temperature": meta["temperature"],
            "max_tokens": meta["max_tokens"],
            "system_prompt": meta["system_prompt"],
        }
    return presets
