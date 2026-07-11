import json
import logging

logger = logging.getLogger(__name__)

def _option_from(value):
    if isinstance(value, dict):
        label = str(value.get("label", "")).strip()
        descr = str(value.get("description", "")).strip()
    elif isinstance(value, str):
        label, descr = value.strip(), ""
    else:
        return None
    if not label:
        return None
    return {"label": label, "description": descr}


def _normalize_batched_questions(value):
    if not isinstance(value, list) or not (1 <= len(value) <= 4):
        raise ValueError("`questions` must contain 1-4 items.")

    questions = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each question must be an object.")

        question = str(item.get("question", "")).strip()
        if not question:
            raise ValueError("Each question needs a non-empty `question`.")

        header = str(item.get("header", "")).strip() or f"Q{index}"
        if len(header) > 12:
            raise ValueError("Each question `header` must be 12 characters or fewer.")

        raw_options = item.get("options") or []
        if not isinstance(raw_options, list) or not (2 <= len(raw_options) <= 4):
            raise ValueError("Each question needs 2-4 `options`.")

        options = []
        for opt in raw_options:
            normalized = _option_from(opt)
            if normalized is not None:
                options.append(normalized)
        if len(options) != len(raw_options):
            raise ValueError("Each option needs a non-empty `label`.")

        questions.append({
            "question": question,
            "header": header,
            "options": options,
            "multiSelect": bool(item.get("multiSelect", False)),
        })
    return questions


class AskUserTool:
    async def execute(self, content, ctx):
        """
        ask_user: the agent poses a multiple-choice question to the user to get a
        decision/clarification. This is a pure UI-control marker — no subprocess,
        no filesystem. It returns an `ask_user` payload that the agent loop turns
        into an `ask_user` SSE event and then ENDS the turn, so the chat waits for
        the user's selection (their choice arrives as the next message).
        """
        question, options, multi = "", [], False
        raw = (content or "").strip()
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}

        if isinstance(parsed, dict) and "questions" in parsed:
            try:
                questions = _normalize_batched_questions(parsed.get("questions"))
            except ValueError as exc:
                return "ask_user: invalid", {
                    "error": f"ask_user invalid questions: {exc}",
                    "exit_code": 1,
                }
            desc = f"ask_user: {len(questions)} questions"
            result = {
                "ask_user": {"questions": questions},
                "output": f"Asked the user {len(questions)} questions. Awaiting their selection.",
                "exit_code": 0,
            }
            logger.info("Tool executed: %s", desc)
            return desc, result

        if isinstance(parsed, dict):
            question = str(parsed.get("question", "")).strip()
            multi = bool(parsed.get("multi") or parsed.get("multiSelect"))
            for opt in (parsed.get("options") or []):
                normalized = _option_from(opt)
                if normalized is not None:
                    options.append(normalized)
        else:
            question = raw

        if not question or len(options) < 2:
            return "ask_user: invalid", {
                "error": (
                    "ask_user needs a non-empty `question` and at least 2 `options` "
                    "(each an object with a `label`, optional `description`)."
                ),
                "exit_code": 1,
            }

        options = options[:6]  # keep the choice list sane
        desc = f"ask_user: {question[:80]}"
        labels = ", ".join(o["label"] for o in options)
        result = {
            "ask_user": {"question": question, "options": options, "multi": multi},
            "output": f"Asked the user: {question}\nOptions: {labels}\nAwaiting their selection.",
            "exit_code": 0,
        }
        logger.info("Tool executed: %s (%d options, multi=%s)", desc, len(options), multi)
        return desc, result

class UpdatePlanTool:
    async def execute(self, content, ctx):
        """
        update_plan: the agent writes back to the active plan — tick an item done
        or revise steps (e.g. when the user asks to change something). Pure UI
        marker: returns a `plan_update` payload the agent loop turns into a
        `plan_update` SSE event; the frontend replaces the stored plan and refreshes
        the docked plan window. Does NOT end the turn.
        """
        raw = (content or "").strip()
        plan = ""
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}

        if isinstance(parsed, dict) and parsed.get("plan"):
            plan = str(parsed.get("plan", "")).strip()
        else:
            plan = raw

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
