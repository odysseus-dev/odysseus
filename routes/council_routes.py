# routes/council_routes.py
"""Council of Models routes — multi-agent named deliberation, debate, and consensus synthesis."""

import os
import json
import uuid
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.database import SessionLocal, ModelEndpoint
from core.middleware import require_admin
from src.auth_helpers import effective_user, owner_filter
from src.endpoint_resolver import build_chat_url, build_headers, resolve_endpoint_runtime, normalize_base
from src.llm_core import stream_llm, llm_call_async, _detect_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/council", tags=["council"])

DATA_DIR = os.getenv("ODYSSEUS_DATA_DIR", "data")
COUNCIL_PRESETS_FILE = os.path.join(DATA_DIR, "council_presets.json")
COUNCIL_HISTORY_FILE = os.path.join(DATA_DIR, "council_history.json")
COUNCIL_LOG_FILE = os.path.join(DATA_DIR, "council_deliberation.log")


def _council_log(msg: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    formatted = f"[{timestamp}] {msg}\n"
    logger.info(f"[Council] {msg}")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(COUNCIL_LOG_FILE)), exist_ok=True)
        with open(COUNCIL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted)
    except Exception:
        pass


class CouncilMember(BaseModel):
    id: str = Field(default_factory=lambda: f"m-{uuid.uuid4().hex[:6]}")
    name: str = Field(..., min_length=1, max_length=50)
    model: str = Field(..., min_length=1)
    endpoint_id: Optional[str] = None
    endpoint_url: Optional[str] = None
    persona: Optional[str] = Field(default="", max_length=500)


class CouncilDiscussRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=4000)
    members: List[CouncilMember] = Field(..., min_length=2, max_length=6)
    rounds: int = Field(default=2, ge=1, le=3)
    synthesis_model: Optional[str] = None
    synthesis_endpoint_id: Optional[str] = None


class CouncilPreset(BaseModel):
    id: str = Field(default_factory=lambda: f"council-{uuid.uuid4().hex[:8]}")
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    members: List[CouncilMember]


def _load_json_file(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")
        return default


def _save_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def _resolve_member_endpoint(member: CouncilMember, owner: Optional[str] = None) -> tuple[str, str, Dict[str, str]]:
    """Resolve endpoint URL, model name, and headers for a council member."""
    db = SessionLocal()
    model_to_use = member.model
    try:
        ep = None
        if member.endpoint_id:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.id == member.endpoint_id)
            ep = owner_filter(q, ModelEndpoint, owner).first()

        if not ep and member.endpoint_url:
            base_norm = normalize_base(member.endpoint_url)
            q = db.query(ModelEndpoint).filter(ModelEndpoint.base_url.ilike(f"%{base_norm}%"))
            ep = owner_filter(q, ModelEndpoint, owner).first()

        if not ep:
            # Fall back to finding an enabled endpoint that carries this model
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            candidates = owner_filter(q, ModelEndpoint, owner).all()
            for cand in candidates:
                raw_models = cand.cached_models or cand.models or "[]"
                try:
                    m_list = json.loads(raw_models) if isinstance(raw_models, str) else raw_models
                except Exception:
                    m_list = []
                if member.model in m_list or any(m.split('/')[-1] == member.model.split('/')[-1] for m in m_list if isinstance(m, str)):
                    ep = cand
                    break

        if ep:
            base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            chat_url = build_chat_url(base)
            headers = build_headers(api_key, base)

            raw_models = ep.cached_models or ep.models or "[]"
            try:
                m_list = json.loads(raw_models) if isinstance(raw_models, str) else raw_models
            except Exception:
                m_list = []
            if model_to_use not in m_list:
                for cand_m in m_list:
                    if cand_m.split('/')[-1] == model_to_use.split('/')[-1]:
                        model_to_use = cand_m
                        break
            return chat_url, model_to_use, headers

        if member.endpoint_url:
            chat_url = build_chat_url(member.endpoint_url)
            headers = build_headers(None, member.endpoint_url)
            return chat_url, model_to_use, headers

        # Fallback to first enabled endpoint if available
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        first_ep = owner_filter(q, ModelEndpoint, owner).first()
        if first_ep:
            base, api_key = resolve_endpoint_runtime(first_ep, owner=owner)
            chat_url = build_chat_url(base)
            headers = build_headers(api_key, base)
            return chat_url, model_to_use, headers

        raise ValueError(f"Could not resolve active endpoint for model '{member.model}' ({member.name})")
    finally:
        db.close()


def setup_council_routes() -> APIRouter:
    """Register Council of Models endpoints."""

    @router.get("/presets")
    async def get_presets():
        presets = _load_json_file(COUNCIL_PRESETS_FILE, [])
        return {"presets": presets}

    @router.post("/presets")
    async def save_presets(request: Request):
        data = await request.json()
        presets = data.get("presets", [])
        _save_json_file(COUNCIL_PRESETS_FILE, presets)
        return {"ok": True, "count": len(presets)}

    @router.get("/history")
    async def get_history():
        history = _load_json_file(COUNCIL_HISTORY_FILE, [])
        return {"history": history}

    @router.post("/history")
    async def save_history(request: Request):
        entry = await request.json()
        if not entry.get("id"):
            entry["id"] = f"delib-{uuid.uuid4().hex[:8]}"
        entry["created_at"] = datetime.now(timezone.utc).isoformat()
        history = _load_json_file(COUNCIL_HISTORY_FILE, [])
        history.insert(0, entry)
        # Retain last 50 deliberations
        history = history[:50]
        _save_json_file(COUNCIL_HISTORY_FILE, history)
        return {"ok": True, "id": entry["id"]}

    @router.delete("/history/{item_id}")
    async def delete_history_item(item_id: str):
        history = _load_json_file(COUNCIL_HISTORY_FILE, [])
        history = [h for h in history if h.get("id") != item_id]
        _save_json_file(COUNCIL_HISTORY_FILE, history)
        return {"ok": True}

    @router.get("/logs")
    async def get_council_logs(lines: int = 150):
        """Retrieve recent council deliberation execution log entries."""
        if not os.path.exists(COUNCIL_LOG_FILE):
            return {"logs": "No council deliberation logs recorded yet."}
        try:
            with open(COUNCIL_LOG_FILE, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                return {"logs": "".join(all_lines[-lines:])}
        except Exception as e:
            return {"logs": f"Error reading logs: {e}"}

    @router.post("/discuss")
    async def discuss_topic(request: Request, body: CouncilDiscussRequest):
        """Sequentially run council debate rounds and stream updates via SSE."""
        owner = effective_user(request)
        topic = body.topic.strip()
        members = body.members
        num_rounds = body.rounds

        _council_log(f"=== New Council Deliberation Convened ===")
        _council_log(f"Topic: '{topic}' | Rounds: {num_rounds} | Members Count: {len(members)}")

        if len(members) < 2 or len(members) > 6:
            _council_log("ERROR: Member count out of bounds (must be 2-6)")
            raise HTTPException(400, "Council must have between 2 and 6 members.")

        # Resolve endpoints for each member
        member_configs = []
        for m in members:
            try:
                url, model, headers = _resolve_member_endpoint(m, owner=owner)
                has_auth = bool(headers.get("Authorization") or headers.get("x-api-key"))
                _council_log(f"Resolved member '{m.name}' -> Model: '{model}', Endpoint URL: '{url}', HasAuth: {has_auth}")
                member_configs.append({
                    "member": m,
                    "url": url,
                    "model": model,
                    "headers": headers,
                })
            except Exception as e:
                err_str = f"Failed to resolve endpoint for member '{m.name}' ({m.model}): {e}"
                _council_log(f"ERROR: {err_str}")
                logger.error(err_str)
                raise HTTPException(400, f"Cannot reach model for member '{m.name}': {e}")

        async def event_generator():
            queue = asyncio.Queue()

            # Helper to yield SSE data
            def _sse_event(event_dict: dict) -> str:
                return f"data: {json.dumps(event_dict)}\n\n"

            # Roster summary for system prompts
            roster_desc = ", ".join(
                f"{m.name} (Persona: {m.persona or 'Expert Council Member'}, Model: {m.model})"
                for m in members
            )

            # Round responses store: { round_num: { member_id: response_text } }
            round_records: Dict[int, Dict[str, str]] = {}

            yield _sse_event({
                "type": "start",
                "topic": topic,
                "rounds": num_rounds,
                "members": [
                    {"id": m.id, "name": m.name, "model": m.model, "persona": m.persona}
                    for m in members
                ]
            })

            # ── Run Each Round Sequentially Turn-by-Turn ──
            # Round-robin conversation history: list of turns with speaker, round, and content
            conversation_history = []

            for r in range(1, num_rounds + 1):
                round_records[r] = {}
                yield _sse_event({
                    "type": "round_start",
                    "round": r,
                    "label": "Opening Arguments & Positions" if r == 1 else f"Deliberation & Cross-Examination (Round {r})"
                })

                for m_idx, cfg in enumerate(member_configs):
                    m = cfg["member"]
                    m_id = m.id
                    m_name = m.name
                    m_url = cfg["url"]
                    m_model = cfg["model"]
                    m_headers = cfg["headers"]

                    persona_ctx = f"Your assigned identity and perspective: {m.persona}." if m.persona else ""

                    # Signal that this specific member has the floor
                    _council_log(f"Round {r} -> Floor given to Councilor '{m_name}' ({m_model})")
                    yield _sse_event({
                        "type": "member_start",
                        "round": r,
                        "member_id": m_id,
                        "member_name": m_name,
                    })

                    # Construct previous speeches context
                    if not conversation_history:
                        # First speaker of Round 1
                        sys_prompt = (
                            f"You are {m_name}, an esteemed member of an expert AI Council.\n"
                            f"{persona_ctx}\n"
                            f"The Council consists of these members: {roster_desc}.\n\n"
                            f"Topic for Deliberation:\n\"{topic}\"\n\n"
                            f"You are the first speaker in the Council to address this topic.\n"
                            f"GUIDELINES:\n"
                            f"1. State your opening position, perspective, and core arguments thoroughly and clearly.\n"
                            f"2. Lay the intellectual foundation for the other councilors to respond to.\n"
                            f"3. Speak freely, straightforwardly, and directly to the Council. Do not overthink or enter prolonged reasoning chains—deliver your full, natural response."
                        )
                        messages = [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": f"Councilor {m_name}, the floor is yours. State your opening position on: \"{topic}\""},
                        ]
                    else:
                        # Build formatted transcript of everything said so far
                        history_lines = []
                        for turn in conversation_history:
                            history_lines.append(f"[{turn['speaker']} - Round {turn['round']}]:\n{turn['content']}\n")
                        transcript_so_far = "\n".join(history_lines)

                        if r == 1:
                            sys_prompt = (
                                f"You are {m_name}, an active member of this AI Council.\n"
                                f"{persona_ctx}\n"
                                f"Council Members: {roster_desc}.\n"
                                f"Topic: \"{topic}\".\n\n"
                                f"Here is what your fellow councilors have stated so far:\n"
                                f"---\n{transcript_so_far}\n---\n\n"
                                f"GUIDELINES:\n"
                                f"1. Acknowledge and directly respond to the points made by previous speakers by name.\n"
                                f"2. State where you agree or disagree, and elaborate freely with your unique insights and reasoning.\n"
                                f"3. Speak straightforwardly, clearly, and directly without excessive internal overthinking or truncation. Complete your full thoughts."
                            )
                            messages = [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": f"Councilor {m_name}, review the opening statements above. Share your thoughts and respond to your colleagues on: \"{topic}\""},
                            ]
                        else:
                            sys_prompt = (
                                f"You are {m_name}, participating in Round {r} of this AI Council.\n"
                                f"{persona_ctx}\n"
                                f"Council Members: {roster_desc}.\n"
                                f"Topic: \"{topic}\".\n\n"
                                f"Complete Deliberation Transcript So Far:\n"
                                f"---\n{transcript_so_far}\n---\n\n"
                                f"OBJECTIVE FOR THIS ROUND:\n"
                                f"1. Cross-examine arguments and directly address colleagues by name.\n"
                                f"2. Identify common ground, resolve disagreements, and refine the Council's direction.\n"
                                f"3. Speak freely, substantively, and straightforwardly without overthinking or truncation."
                            )
                            messages = [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": f"Councilor {m_name}, review the debate transcript above. Provide your cross-examination and insights."},
                            ]

                    if await request.is_disconnected():
                        _council_log("Client disconnected; stopping Council debate.")
                        return

                    accumulated = []
                    try:
                        async for chunk in stream_llm(
                            url=m_url,
                            model=m_model,
                            messages=messages,
                            temperature=0.7,
                            max_tokens=2048,
                            headers=m_headers,
                            timeout=120,
                            workload="foreground"
                        ):
                            if await request.is_disconnected():
                                _council_log("Client disconnected; aborting turn.")
                                return
                            for line in chunk.splitlines():
                                line = line.strip()
                                if not line or "event: error" in line:
                                    continue
                                if line.startswith("data:"):
                                    data_part = line[5:].strip()
                                    if not data_part or data_part == "[DONE]":
                                        continue
                                    try:
                                        payload = json.loads(data_part)
                                        if isinstance(payload, dict):
                                            if payload.get("error") or (payload.get("status") and payload.get("status") >= 400):
                                                err_msg = str(payload.get("error") or payload.get("text") or f"HTTP {payload.get('status')}")
                                                _council_log(f"Error chunk from '{m_name}': {err_msg}")
                                                yield _sse_event({
                                                    "type": "member_error",
                                                    "round": r,
                                                    "member_id": m_id,
                                                    "member_name": m_name,
                                                    "error": err_msg,
                                                })
                                                err_delta = f"\n\n*(Error: {err_msg})*"
                                                accumulated.append(err_delta)
                                                yield _sse_event({
                                                    "type": "member_chunk",
                                                    "round": r,
                                                    "member_id": m_id,
                                                    "member_name": m_name,
                                                    "delta": err_delta,
                                                })
                                            else:
                                                delta = payload.get("delta") or payload.get("content") or ""
                                                if delta:
                                                    accumulated.append(delta)
                                                    yield _sse_event({
                                                        "type": "member_chunk",
                                                        "round": r,
                                                        "member_id": m_id,
                                                        "member_name": m_name,
                                                        "delta": delta,
                                                    })
                                    except Exception:
                                        pass
                    except Exception as e:
                        err_msg = f"\n\n*(Error from {m_name}: {str(e)})*"
                        _council_log(f"Exception during '{m_name}' stream: {e}")
                        yield _sse_event({
                            "type": "member_error",
                            "round": r,
                            "member_id": m_id,
                            "member_name": m_name,
                            "error": str(e),
                        })
                        accumulated.append(err_msg)
                        yield _sse_event({
                            "type": "member_chunk",
                            "round": r,
                            "member_id": m_id,
                            "member_name": m_name,
                            "delta": err_msg,
                        })

                    full_text = "".join(accumulated).strip()
                    _council_log(f"Councilor '{m_name}' (Round {r}) Stated:\n{full_text if full_text else '(No response)'}\n----------------------------------------")
                    round_records[r][m_id] = full_text
                    conversation_history.append({
                        "speaker": m_name,
                        "member_id": m_id,
                        "persona": m.persona,
                        "round": r,
                        "content": full_text or "(No statement recorded)",
                    })

                    yield _sse_event({
                        "type": "member_done",
                        "round": r,
                        "member_id": m_id,
                        "member_name": m_name,
                        "content": full_text,
                    })

                yield _sse_event({
                    "type": "round_end",
                    "round": r,
                })

            # ── Final Stage: Consensus Synthesis ──
            _council_log("Starting Council Consensus Synthesis")
            yield _sse_event({
                "type": "synthesis_start",
                "label": "Synthesizing Council Consensus & Unified Answer",
            })

            # Build full multi-round transcript
            full_transcript_parts = []
            for r_num in range(1, num_rounds + 1):
                full_transcript_parts.append(f"=== ROUND {r_num} ===")
                for cfg in member_configs:
                    m = cfg["member"]
                    stmt = round_records.get(r_num, {}).get(m.id, "")
                    full_transcript_parts.append(f"[{m.name} ({m.model})]:\n{stmt}\n")
            full_transcript = "\n".join(full_transcript_parts)

            synth_sys_prompt = (
                "You are the Presiding Chair of the AI Council.\n"
                "The Council members have concluded their debate on the user's topic.\n\n"
                f"Topic: \"{topic}\"\n\n"
                f"Full Deliberation Transcript:\n"
                f"----------------------------------------\n"
                f"{full_transcript}\n"
                f"----------------------------------------\n\n"
                "YOUR TASK:\n"
                "Synthesize the entire discussion into the authoritative **Council Verdict & Consensus Answer**.\n"
                "Structure your response as follows:\n"
                "1. **Executive Consensus**: The unified stance agreed upon by the Council.\n"
                "2. **Key Insights & Distinct Viewpoints**: Notable perspectives and trade-offs highlighted by specific members (mention them by name, e.g. Joana, Roseann).\n"
                "3. **Unified Recommendation**: A comprehensive, direct, and actionable final answer answering the user's topic.\n\n"
                "Format cleanly using Markdown (headers, bullet points, bold key terms)."
            )

            synth_messages = [
                {"role": "system", "content": synth_sys_prompt},
                {"role": "user", "content": f"Deliver the Council's final consensus verdict on: \"{topic}\""},
            ]

            # Choose synthesis model: first member's config or user specified
            synth_cfg = member_configs[0]
            if body.synthesis_model:
                for cfg in member_configs:
                    if cfg["model"] == body.synthesis_model:
                        synth_cfg = cfg
                        break

            synth_accumulated = []
            try:
                async for chunk in stream_llm(
                    url=synth_cfg["url"],
                    model=synth_cfg["model"],
                    messages=synth_messages,
                    temperature=0.5,
                    max_tokens=4096,
                    headers=synth_cfg["headers"],
                    timeout=120,
                    workload="foreground"
                ):
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line or "event: error" in line:
                            continue
                        if line.startswith("data:"):
                            data_part = line[5:].strip()
                            if not data_part or data_part == "[DONE]":
                                continue
                            try:
                                payload = json.loads(data_part)
                                if isinstance(payload, dict):
                                    if payload.get("error") or (payload.get("status") and payload.get("status") >= 400):
                                        err_msg = str(payload.get("error") or payload.get("text") or f"HTTP {payload.get('status')}")
                                        err_delta = f"\n\n*(Synthesis Error: {err_msg})*"
                                        synth_accumulated.append(err_delta)
                                        yield _sse_event({
                                            "type": "synthesis_chunk",
                                            "delta": err_delta,
                                        })
                                    else:
                                        delta = payload.get("delta") or payload.get("content") or ""
                                        if delta:
                                            synth_accumulated.append(delta)
                                            yield _sse_event({
                                                "type": "synthesis_chunk",
                                                "delta": delta,
                                            })
                            except Exception:
                                pass
            except Exception as e:
                err_text = f"\n\n*(Synthesis generation error: {str(e)})*"
                synth_accumulated.append(err_text)
                yield _sse_event({
                    "type": "synthesis_chunk",
                    "delta": err_text,
                })

            final_verdict = "".join(synth_accumulated).strip()
            _council_log(f"Council Verdict & Consensus Answer:\n{final_verdict}\n========================================")
            yield _sse_event({
                "type": "synthesis_done",
                "verdict": final_verdict,
            })

            yield _sse_event({"type": "complete"})

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    return router
