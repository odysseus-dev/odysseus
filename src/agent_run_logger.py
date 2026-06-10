"""Per-run JSONL logging for agent task executions (overseer stage 1).

One file per task run: logs/agent_runs/<task_id>/<run_id>.jsonl. Every line
is a self-contained event: executor rounds (LLM text + tool calls/results),
supervisor verdicts, Telegram interactions, checkpoint/resume marks. The
file is the ground truth for debugging both agents after the fact.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "logs" / "agent_runs"
_LOCK = threading.Lock()


class AgentRunLogger:
    """Append-only JSONL writer for one task run. Never raises."""

    def __init__(self, task_id: str, run_id: str):
        self.task_id = task_id
        self.run_id = run_id
        self.path = _BASE_DIR / _sanitize(task_id) / f"{_sanitize(run_id)}.jsonl"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"agent run log dir failed: {e}")

    def log(self, event_type: str, **fields):
        record = {"ts": round(time.time(), 3), "type": event_type, **fields}
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except Exception:
            line = json.dumps({"ts": record["ts"], "type": event_type,
                               "error": "unserializable event"})
        try:
            with _LOCK, open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.debug(f"agent run log write failed: {e}")

    # Convenience wrappers keep call sites short and the event schema uniform.
    def round_end(self, round_num: int, round_response: str, tool_events: list, stats: dict):
        # Only this round's tool events — tool_events accumulates across rounds.
        current = [e for e in tool_events if e.get("round") == round_num]
        self.log("round_end", round=round_num, response=round_response,
                 tools=current, stats=stats)

    def run_started(self, prompt: str, model: str, endpoint_url: str, resumed_from_round: int = 0):
        self.log("run_started", prompt=prompt, model=model,
                 endpoint_url=endpoint_url, resumed_from_round=resumed_from_round)

    def run_finished(self, status: str, result: str = "", error: str = ""):
        self.log("run_finished", status=status, result=result, error=error)


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))[:80]
