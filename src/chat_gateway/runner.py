"""Chat Gateway — shared agent runner.

One instance is shared by every platform adapter. For each inbound message it:
  1. gates on require_mention / channel allowlist,
  2. resolves the owner's chat endpoint + headers (mirrors task_scheduler._run_agent_loop),
  3. keeps a persistent Odysseus session per (platform, channel) so the agent
     has conversation memory,
  4. runs the FULL agent via src.agent_loop.stream_agent_loop and collects the
     reply text from the SSE event stream,
  5. returns an OutgoingMessage for the adapter to deliver.

This is the only place agent logic lives — adapters are pure transport.
All app imports are lazy (done inside methods) to match the codebase style and
avoid import-time coupling.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, Optional, Set, Tuple

from .base import IncomingMessage, OutgoingMessage
from .config import GatewayConfig, PlatformConfig

logger = logging.getLogger("chat_gateway")

# Generous ceiling; conversation turns shouldn't run as long as scheduled tasks.
_MAX_ROUNDS = 20

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove model chain-of-thought so it never reaches the chat platform.
    Handles paired <think>...</think> blocks and stray/unclosed tags."""
    text = _THINK_BLOCK.sub("", text)
    # Unmatched close tag: keep only what follows the last </think>.
    if "</think>" in text.lower():
        idx = text.lower().rfind("</think>")
        text = text[idx + len("</think>"):]
    # Unmatched open tag with no close: drop everything from it onward is wrong
    # (the answer usually follows), so just strip the bare tag.
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
    return text.strip()


class GatewayRunner:
    def __init__(self, cfg: GatewayConfig, session_manager):
        self.cfg = cfg
        self.session_manager = session_manager
        # (platform, channel_id) -> odysseus session_id
        self._sessions: Dict[Tuple[str, str], str] = {}
        # An external chat gateway must never expose admin/host-execution tools.
        # If the resolved owner is an admin (or single-user mode is on), the agent
        # would otherwise get the full toolset (bash/python/...). Force the
        # public-user policy unless the operator explicitly opts in.
        self._force_public = self._compute_force_public()

    def _compute_force_public(self) -> bool:
        try:
            from src.tool_security import owner_is_admin_or_single_user
            privileged = owner_is_admin_or_single_user(self.cfg.owner or None)
        except Exception:
            privileged = True  # fail closed
        if privileged and not self.cfg.allow_privileged_owner:
            logger.warning(
                "chat_gateway: owner %r is an admin or single-user mode is active. "
                "Forcing the public-user toolset (no bash/python/file/email/memory, "
                "MCP off) so this external gateway cannot reach host execution. Use a "
                "dedicated non-admin 'owner', or set 'allow_privileged_owner: true' to "
                "override (NOT recommended for an externally reachable bot).",
                self.cfg.owner,
            )
            return True
        return False

    # ── public entrypoint handed to adapters via set_message_handler ────
    async def handle_message(self, msg: IncomingMessage) -> Optional[OutgoingMessage]:
        pcfg = self.cfg.platforms.get(msg.platform)
        if pcfg is None or not pcfg.enabled:
            return None
        if not self._should_respond(pcfg, msg):
            return None
        if not msg.text.strip():
            return None

        reply_text = await self._run_agent(pcfg, msg)
        if not reply_text or not reply_text.strip():
            return None
        # Reply inline. Only continue an existing thread; never start a new one
        # off a root-level message (that buries the reply in the sidebar).
        thread = msg.thread_id or None
        return OutgoingMessage(channel_id=msg.channel_id, text=reply_text, thread_id=thread)

    # ── gating ──────────────────────────────────────────────────────────
    def _should_respond(self, pcfg: PlatformConfig, msg: IncomingMessage) -> bool:
        # DMs are always answered (no mention needed).
        if msg.is_direct:
            return True
        # Optional allowlist of channels the bot will engage in at all.
        if pcfg.channels and msg.channel_id not in pcfg.channels:
            return False
        # Interactive channels: respond without a mention.
        if msg.channel_id in pcfg.free_response_channels:
            return True
        # Everywhere else: require an @mention if configured.
        if pcfg.require_mention and not msg.was_mentioned:
            return False
        return True

    # ── session reuse for conversation memory ───────────────────────────
    def _session_id_for(self, msg: IncomingMessage, endpoint_url: str, model: str, headers: dict) -> str:
        from core.models import ChatMessage  # noqa: F401 (ensures models loaded)
        key = (msg.platform, msg.channel_id)
        sid = self._sessions.get(key)
        if sid:
            try:
                self.session_manager.get_session(sid)
                return sid
            except Exception:
                self._sessions.pop(key, None)  # stale; recreate below
        import uuid
        sid = str(uuid.uuid4())
        sess = self.session_manager.create_session(
            session_id=sid,
            name=f"{msg.platform}:{msg.channel_id}",
            endpoint_url=endpoint_url,
            model=model,
            owner=self.cfg.owner or None,
        )
        if headers:
            sess.headers = headers
        self.session_manager.save_sessions()
        self._sessions[key] = sid
        return sid

    # ── endpoint/header resolution (mirrors task_scheduler._run_agent_loop) ─
    def _resolve_endpoint(self, pcfg: PlatformConfig):
        from core.database import SessionLocal, ModelEndpoint
        from src.auth_helpers import owner_filter
        from src.endpoint_resolver import normalize_base, build_chat_url, build_headers

        owner = self.cfg.owner or None
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
            q = owner_filter(q, ModelEndpoint, owner)
            ep = q.order_by(ModelEndpoint.owner.desc(), ModelEndpoint.created_at).first()
            if ep is None:
                return None, None, {}
            base_url = normalize_base(ep.base_url)
            api_key = ep.api_key
            # Provider-auth (OAuth-style) endpoints resolve credentials at runtime.
            if getattr(ep, "provider_auth_id", None):
                try:
                    from src.endpoint_resolver import resolve_endpoint_runtime
                    base_url, api_key = resolve_endpoint_runtime(ep, owner=owner)
                    base_url = normalize_base(base_url)
                except Exception:
                    logger.warning("chat_gateway: provider-auth resolve failed; using stored values")
            endpoint_url = build_chat_url(base_url)
            headers = build_headers(api_key, base_url) if api_key else {}
            # Model: explicit config override → endpoint's first cached model → "auto"
            model = pcfg.options.get("model")
            if not model:
                try:
                    cached = json.loads(ep.cached_models or "[]")
                    model = cached[0] if cached else "auto"
                except Exception:
                    model = "auto"
            return endpoint_url, model, headers
        finally:
            db.close()

    # ── enforced tool restrictions → (disabled_tools, disable_mcp) ───────
    def _tool_restrictions(self, pcfg: PlatformConfig) -> Tuple[Set[str], bool]:
        """Return the ENFORCED restrictions for a gateway agent run. These are
        applied via disabled_tools + ToolPolicy (both honoured by the agent
        loop), never as soft hints."""
        from src.tool_security import NON_ADMIN_BLOCKED_TOOLS
        disabled: Set[str] = set()
        disable_mcp = False

        # (1) Never expose admin/host-execution tools through an external gateway.
        if self._force_public:
            disabled |= set(NON_ADMIN_BLOCKED_TOOLS)
            disable_mcp = True

        ts = pcfg.toolsets
        if ts.mode == "deny" and ts.deny:
            disabled |= set(ts.deny)
        elif ts.mode == "allow":
            # (3) Real allow-listing: disable every known tool NOT explicitly
            # allowed (enforced, not a hint). Dynamic MCP tools can't be named in
            # advance, so MCP is disabled unless an mcp__ tool is explicitly allowed.
            allowed = {str(a) for a in (ts.allow or [])}
            disabled |= (self._all_tool_names() - allowed)
            if not any(a.startswith("mcp__") for a in allowed):
                disable_mcp = True
        return disabled, disable_mcp

    @staticmethod
    def _all_tool_names() -> Set[str]:
        """Every known function-tool name, for inverting an allow-list into a
        denylist (mirrors tool_security.plan_mode_disabled_tools). Fails closed:
        on a schema-import failure, still returns the public block set."""
        names: Set[str] = set()
        try:
            import src.agent_tools  # noqa: F401  (resolves the circular schema import)
            from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
            for t in FUNCTION_TOOL_SCHEMAS:
                n = (t.get("function") or {}).get("name")
                if n:
                    names.add(n)
        except Exception as exc:
            logger.warning("chat_gateway: tool-schema enumeration failed (%s); "
                           "allow-mode falls back to the public block set", exc)
        from src.tool_security import NON_ADMIN_BLOCKED_TOOLS
        return names | set(NON_ADMIN_BLOCKED_TOOLS)

    # ── run the full agent and collect the reply ────────────────────────
    async def _run_agent(self, pcfg: PlatformConfig, msg: IncomingMessage) -> str:
        from core.models import ChatMessage
        from src.agent_loop import stream_agent_loop

        endpoint_url, model, headers = self._resolve_endpoint(pcfg)
        if not endpoint_url:
            logger.warning("chat_gateway: no enabled ModelEndpoint for owner=%r", self.cfg.owner)
            return "I'm not configured with a model endpoint yet — ask the admin to add one in Settings."

        sid = self._session_id_for(msg, endpoint_url, model, headers)
        sess = self.session_manager.get_session(sid)
        sess.add_message(ChatMessage("user", msg.text))
        messages = [{"role": m.role, "content": m.content} for m in sess.history]

        disabled, disable_mcp = self._tool_restrictions(pcfg)
        tool_policy = None
        if disabled or disable_mcp:
            from src.tool_policy import ToolPolicy
            tool_policy = ToolPolicy(disabled_tools=frozenset(disabled), disable_mcp=disable_mcp)

        try:
            from src.endpoint_resolver import resolve_utility_fallback_candidates
            fallbacks = resolve_utility_fallback_candidates(owner=self.cfg.owner or None)
        except Exception:
            fallbacks = []

        full_text = ""
        tool_results = []
        try:
            async for event_str in stream_agent_loop(
                endpoint_url=endpoint_url,
                model=model,
                messages=messages,
                headers=headers,
                max_rounds=_MAX_ROUNDS,
                session_id=sid,
                owner=self.cfg.owner or None,
                disabled_tools=(disabled or None),
                tool_policy=tool_policy,
                fallbacks=fallbacks,
            ):
                if not event_str.startswith("data: ") or event_str.startswith("data: [DONE]"):
                    continue
                try:
                    data = json.loads(event_str[6:])
                except (json.JSONDecodeError, KeyError):
                    continue
                if "delta" in data:
                    if data.get("thinking"):
                        continue
                    full_text += data["delta"]
                elif data.get("type") == "tool_output":
                    summary = data.get("stdout") or data.get("output") or data.get("result") or ""
                    if isinstance(summary, str) and summary.strip():
                        tool_results.append(f"[{data.get('tool', '?')}] {summary[:300]}")
        except Exception:
            logger.exception("chat_gateway: agent loop failed")
            return "Sorry — I hit an error while processing that."

        full_text = _strip_think(full_text or "")
        if full_text:
            sess.add_message(ChatMessage("assistant", full_text))
            self.session_manager.save_sessions()
            return full_text

        # No final text (e.g. ran out of rounds mid-tool-use): surface tool work.
        if tool_results:
            return "\n".join(tool_results[-5:])
        return "(no response)"
