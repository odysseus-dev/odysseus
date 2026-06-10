
# [!] A NEW CODES, Clean up redundant headers...
# ===========================================================================
# 1. CREATE A NEW HANDLER FUNCTIONS HERE (outside the main function)
# ===========================================================================
import json as _json
import logging

logger = logging.getLogger(__name__)

class WorkflowTools:

    """
    Handles standalone agent workflow tools (ask_user, update_plan).
    Separated from the core execution block to maintain the coordinator pattern.
    """

    @staticmethod
    async def ask_user(content: str, **kwargs) -> tuple[str, dict]:
        """
        Handles the ask_user tool execution with robust JSON vs Plain-text routing.
        
        This function processes input that can either be a structured JSON payload 
        (for multiple-choice queries) or a raw plain-text string (for open-ended questions).
        It ensures strict validation so that malformed or incomplete JSON structures 
        are properly rejected instead of falling through as valid plain text.
        """
        import json as _json
        raw = (content or "").strip()
        
        # Initialize state flags and parsing variables
        is_json_payload = False
        parsed = None

        # Determine input type by safely attempting to decode it as a JSON object
        try:
            decoded = _json.loads(raw)
            if isinstance(decoded, dict):
                parsed = decoded
                is_json_payload = True
        except (ValueError, TypeError):
            # Decoding failed or result is not a dictionary; treat as plain text
            pass
         
        # JSON Payload Routing (Multiple-Choice Sceanario)
        if is_json_payload and parsed is not None:
            # Strict validation: The JSON structure MUST contain a 'question' key
            if "question" not in parsed:
                return "ask_user: invalid", {
                    "error": "ask_user needs a non-empty `question`.",
                    "exit_code": 1,
                }

            question = str(parsed.get("question", "")).strip()
            multi = bool(parsed.get("multi") or parsed.get("multiSelect"))
            raw_opts = parsed.get("options") or []
            
            # Normalize options format into standard {"label": ..., "description": ...}
            options = [
                {"label": str(o.get("label", "")).strip(), "description": str(o.get("description", "")).strip()}
                if isinstance(o, dict) else {"label": str(o).strip(), "description": ""}
                for o in raw_opts if o
            ]
            # Filter out empty options and enforce a maximum limit of 6 for UI sanity
            options = [o for o in options if o["label"]][:6]

            # Multiple-choice payloads must provide at least 2 valid options to be interactive
            if len(options) < 2:
                return "ask_user: invalid", {
                    "error": "ask_user JSON payload requires at least 2 valid options.",
                    "exit_code": 1,
                }

        # Plain Text Routing (Open-Ended Query Scenario)
        else:
            question = raw
            options = []
            multi = False

        # Global Guard Clause: Ensure the extracted question is not empty
        if not question:
            return "ask_user: invalid", {
                "error": "ask_user needs a non-empty `question`.",
                "exit_code": 1,
            }
            
        desc = f"ask_user: {question[:80]}"
        labels = ", ".join(o["label"] for o in options)
        result = {
            "ask_user": {"question": question, "options": options, "multi": multi},
            "output": f"Asked the user: {question}\nOptions: {labels}\nAwaiting their selection.",
            "exit_code": 0,
        }
        
        logger.info("Tool executed: %s (%d options, multi=%s)", desc, len(options), multi)
        return desc, result

    async def update_plan(content: str, **kwargs) -> tuple[str, dict]:
        """
        Handles the update_plan tool execution.
        
        The agent updates the active task checklist, marking items complete or revising 
        steps based on ongoing operations. This returns a 'plan_update' payload that 
        the agent loop dispatches as an SSE event to update and refresh the front-end 
        plan window view. This operation does NOT terminate the current agent turn.
        """
        import json as _json
        raw = (content or "").strip()
        
        # Attempt to extract 'plan' from JSON, fallback to raw string if parsing fails
        try:
            parsed = _json.loads(raw) if raw.startswith("{") else None
            plan = str(parsed.get("plan", "")).strip() if isinstance(parsed, dict) and parsed else raw.strip()
        except (ValueError, TypeError):
            plan = raw

        # Guard Clause: ensure the plan content is not empty
        if not plan:
            return "update_plan: invalid", {
                "error": "update_plan needs a non-empty `plan` (the full updated checklist as markdown).",
                "exit_code": 1,
            }

        plan = plan[:8192]
        done = plan.count("- [x]") + plan.count("- [X]")
        total = done + plan.count("- [ ]")
        
        desc = f"update_plan: {done}/{total} done" if total else "update_plan"
        result = {
            "plan_update": {"plan": plan},
            "output": f"Plan updated ({done}/{total} steps complete)." if total else "Plan updated.",
            "exit_code": 0,
        }
        
        logger.info("Tool executed: %s", desc)
        return desc, result
        