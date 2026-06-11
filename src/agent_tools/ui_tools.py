import logging

logger = logging.getLogger(__name__)


class UIControlTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Control frontend UI: toggle settings, switch model, change theme.

        Content format:
        Line 1: action
        Line 2+: action-specific params

        Actions:
        toggle <name> <on|off>  — Toggle a setting (web, bash, rag, research, incognito, document_editor)
        set_mode <agent|chat>   — Switch between agent and chat mode
        switch_model <model>    — Change the model for the current session
        set_theme <preset>      — Apply a built-in theme preset (dark, light, midnight, paper, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, organs, lavender, gpt, claude, cute)
        create_theme <name> <bg> <fg> <panel> <border> <accent> [key=val ...] — Create custom theme. Optional key=val: advanced color overrides AND background effects: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num>, bgEffectSize=<num>, frosted=true|false
        open_panel <name>       — Open a panel (documents, gallery, email, sessions, notes, memories, skills, settings, cookbook)
        open_email_reply <uid> [folder] [reply|reply-all|ai-reply] — Open a reply draft document for an email; does not send
        get_toggles             — Return current toggle states (server-side knowledge)"""
        owner = ctx.get("owner")
        session_id = ctx.get("session_id")
        from src.ai_interaction import _resolve_model, _session_manager

        lines = content.strip().split("\n")
        if not lines:
            return {"error": "No action specified"}

        parts = lines[0].strip().split(None, 2)
        action = parts[0].lower()

        if action == "toggle":
            if len(parts) < 3:
                return {"error": "toggle needs: toggle <name> <on|off>"}
            toggle_name = parts[1].lower()
            state = parts[2].lower() in ("on", "true", "1", "yes", "enable", "enabled")
            # Friendly aliases — users say "shell" / "search" naturally.
            _toggle_aliases = {
                "shell": "bash",
                "terminal": "bash",
                "search": "web", 
                "websearch": "web", 
                "web_search": "web",
                "deepresearch": "research", 
                "deep_research": "research",
                "documents": "document_editor", 
                "doc": "document_editor", 
                "docs": "document_editor",
                "private": "incognito",
            }
            toggle_name = _toggle_aliases.get(toggle_name, toggle_name)
            valid_toggles = {"web", "bash", "rag", "research", "incognito", "document_editor"}
            if toggle_name not in valid_toggles:
                return {"error": f"Unknown toggle '{toggle_name}'. Valid: {', '.join(sorted(valid_toggles))}"}
            return {
                "ui_event": "toggle",
                "toggle_name": toggle_name,
                "state": state,
                "results": f"Toggle '{toggle_name}' set to {'on' if state else 'off'}",
            }

        elif action == "set_mode":
            if len(parts) < 2:
                return {"error": "set_mode needs: set_mode <agent|chat>"}
            mode = parts[1].lower()
            if mode not in ("agent", "chat"):
                return {"error": f"Invalid mode '{mode}'. Use: agent, chat"}
            return {
                "ui_event": "set_mode",
                "mode": mode,
                "results": f"Mode changed to '{mode}'",
            }

        elif action == "switch_model":
            model_spec = " ".join(parts[1:]) if len(parts) > 1 else ""
            if not model_spec:
                model_spec = lines[1].strip() if len(lines) > 1 else ""
            if not model_spec:
                return {"error": "switch_model needs a model name"}

            # Resolve the model to validate it exists
            try:
                url, model_id, headers = _resolve_model(model_spec, owner=owner)
            except ValueError as e:
                return {"error": str(e)}

            # Update current session's model if we have a session
            if session_id and _session_manager:
                from src.database import SessionLocal as SL2, Session as DbSess2
                db2 = SL2()
                try:
                    db_s = db2.query(DbSess2).filter(DbSess2.id == session_id).first()
                    if db_s:
                        db_s.endpoint_url = url
                        db_s.model = model_id
                        db2.commit()
                finally:
                    db2.close()

                sess = _session_manager.get_session(session_id)
                if sess:
                    sess.endpoint_url = url
                    sess.model = model_id
                    if headers:
                        sess.headers = headers

            return {
                "ui_event": "switch_model",
                "model": model_id,
                "endpoint_url": url,
                "results": f"Model switched to '{model_id}'",
            }

        elif action == "set_theme":
            theme_name = parts[1].lower() if len(parts) > 1 else ""
            # Theme colors are defined in static/js/theme.js on the frontend.
            # We pass the name; the frontend looks it up from presets + custom themes.
            # Also check user's custom themes stored in prefs.
            # Must match the THEMES keys in static/js/theme.js.
            known_presets = [
                "dark", "light", "midnight", "paper", "cyberpunk", "retrowave",
                "forest", "ocean", "ume", "copper", "terminal", "organs",
                "lavender", "gpt", "claude", "cute",
            ]
            custom_themes = {}
            try:
                from routes.prefs_routes import _load as _load_prefs
                custom_themes = _load_prefs().get("custom-themes", {}) or {}
            except Exception:
                pass
            all_known = set(known_presets) | set(custom_themes.keys())
            if theme_name not in all_known:
                custom_label = f" | Custom: {', '.join(sorted(custom_themes.keys()))}" if custom_themes else ""
                return {"error": f"Unknown theme '{theme_name}'. Available: {', '.join(sorted(known_presets))}{custom_label}"}
            return {
                "ui_event": "set_theme",
                "theme_name": theme_name,
                "results": f"Theme changed to '{theme_name}'",
            }

        elif action == "create_theme":
            # Re-split without limit to get all parts
            parts_all = lines[0].strip().split()
            # create_theme <name> <bg> <fg> <panel> <border> <accent> [key=value ...]
            if len(parts_all) < 7:
                return {"error": "create_theme needs: create_theme <name> <bg> <fg> <panel> <border> <accent> (all hex colors). Optional advanced color key=value pairs and background effects."}
            name = parts_all[1].lower().replace(" ", "-")
            colors = {"bg": parts_all[2], "fg": parts_all[3], "panel": parts_all[4], "border": parts_all[5], "red": parts_all[6]}
            import re as _re
            for k, v in colors.items():
                if not _re.match(r'^#[0-9a-fA-F]{6}$', v):
                    return {"error": f"Invalid hex color for {k}: '{v}'. Use format #RRGGBB"}
            # Parse optional advanced key=value pairs
            adv_keys = {
                "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
                "sectionAccent", "brandColor", "inputBg", "inputBorder",
                "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
                "toggleBg", "toggleActive", "accentPrimary", "accentError",
            }
            advanced = {}
            # Background-effect fields (animated pattern + frosted glass). Different
            # value types than the hex-only advanced keys, so parse separately.
            _BG_PATTERNS = {"none", "dots", "synapse", "rain", "constellations",
                            "perlin-flow", "petals", "sparkles", "embers"}
            bg = {}
            for part in parts_all[7:]:
                if "=" not in part:
                    continue
                ak, av = part.split("=", 1)
                if ak in adv_keys:
                    if not _re.match(r'^#[0-9a-fA-F]{6}$', av):
                        return {"error": f"Invalid hex color for advanced key {ak}: '{av}'. Use format #RRGGBB"}
                    advanced[ak] = av
                elif ak == "bgPattern":
                    if av not in _BG_PATTERNS:
                        return {"error": f"Invalid bgPattern '{av}'. Use one of: {', '.join(sorted(_BG_PATTERNS))}"}
                    bg["pattern"] = av
                elif ak == "bgEffectColor":
                    if not _re.match(r'^#[0-9a-fA-F]{6}$', av):
                        return {"error": f"Invalid hex color for bgEffectColor: '{av}'. Use format #RRGGBB"}
                    bg["effectColor"] = av
                elif ak in ("bgEffectIntensity", "bgEffectSize"):
                    try:
                        bg["effectIntensity" if ak == "bgEffectIntensity" else "effectSize"] = float(av)
                    except ValueError:
                        return {"error": f"Invalid number for {ak}: '{av}'"}
                elif ak == "frosted":
                    bg["frosted"] = av.lower() in ("true", "1", "yes", "on")
            if advanced:
                colors["advanced"] = advanced
            return {
                "ui_event": "create_theme",
                "theme_name": name,
                "colors": colors,
                "bg": bg or None,
                "results": f"Custom theme '{name}' created and applied"
                           + (f" with {len(advanced)} advanced overrides" if advanced else "")
                           + (f" + background effect ({bg.get('pattern', 'frosted' if bg.get('frosted') else 'custom')})" if bg else ""),
            }

        elif action == "highlight":
            selector = parts[1] if len(parts) > 1 else ""
            label = " ".join(parts[2:]) if len(parts) > 2 else ""
            if not selector:
                return {"error": "highlight needs: highlight <css-selector> [label]"}
            return {
                "ui_event": "highlight",
                "selector": selector,
                "label": label,
                "results": f"Highlighting '{selector}'",
            }

        elif action == "clear_highlight":
            return {
                "ui_event": "clear_highlight",
                "results": "Highlights cleared",
            }

        elif action == "open_panel":
            # Open a top-level panel/modal: documents/library, gallery,
            # email, sessions, notes, memories, skills, settings, cookbook.
            panel = parts[1].lower() if len(parts) > 1 else ""
            _panel_aliases = {
                "documents": "documents", "document": "documents", "doc": "documents", "docs": "documents",
                "library": "documents", "doclib": "documents",
                "gallery": "gallery", "images": "gallery",
                "email": "email", "emails": "email", "inbox": "email", "mail": "email",
                "sessions": "sessions", "chats": "sessions", "history": "sessions",
                "notes": "notes", "note": "notes", "todo": "notes", "todos": "notes",
                "memories": "memories", "memory": "memories", "brain": "memories",
                "skills": "skills",
                "settings": "settings", "preferences": "settings",
                "cookbook": "cookbook", "models": "cookbook", "llm": "cookbook", "serve": "cookbook", "serving": "cookbook",
            }
            target = _panel_aliases.get(panel)
            if not target:
                return {"error": f"Unknown panel '{panel}'. Valid: documents, gallery, email, sessions, notes, memories, skills, settings, cookbook."}
            return {
                "ui_event": "open_panel",
                "panel": target,
                "results": f"Opening {target} panel",
            }

        elif action == "open_email_reply":
            reply_parts = lines[0].strip().split()
            uid = reply_parts[1].strip() if len(reply_parts) > 1 else ""
            folder = reply_parts[2].strip() if len(reply_parts) > 2 else "INBOX"
            mode = reply_parts[3].strip().lower() if len(reply_parts) > 3 else "reply"
            if not uid:
                return {"error": "open_email_reply needs: open_email_reply <uid> [folder] [reply|reply-all|ai-reply]"}
            if mode not in ("reply", "reply-all", "ai-reply"):
                mode = "reply"
            return {
                "ui_event": "open_email_reply",
                "uid": uid,
                "folder": folder or "INBOX",
                "mode": mode,
                "results": f"Opening reply draft for email UID {uid}",
            }

        elif action == "get_toggles":
            return {
                "results": (
                    "Toggle states are managed client-side in localStorage. "
                    "Available toggles: web, bash, rag, research, incognito, document_editor. "
                    "Use 'toggle <name> <on|off>' to change them."
                )
            }

        else:
            return {"error": f"Unknown action '{action}'. Use: toggle, set_mode, switch_model, set_theme, highlight, clear_highlight, get_toggles"}
