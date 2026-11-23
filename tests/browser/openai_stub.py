"""A deterministic OpenAI-compatible model for the Notes browser runs.

Usage: openai_stub.py PORT LOG_PATH

Answers /v1/models with one model and /v1/chat/completions, streamed or not.
Every completion request body is appended to LOG_PATH as one JSON line, so a
test can read exactly which prompt and which tools reached the model.

Replies:
- after a tool result: "Tool result: <the result text>";
- a user message containing `SHARETEST <note-id> <user>`, when manage_notes is
  offered: one manage_notes `share` call for that note and user;
- a run whose focus todo is `EDITTEST <word>`, when manage_notes is offered:
  one manage_notes `update` of the attached note (its `Id:` in the context),
  content `Edited by the agent: <word>`;
- a run whose focus todo is `EDITNUL <word>`, when manage_notes is offered:
  the same update, with a NUL after the attached note's id;
- a run whose focus todo is `EDITOTHER <note-id>`, when manage_notes is
  offered: one manage_notes `update` of that other note;
- a run whose focus todo is `EDITTWO <word>`, when manage_notes is offered:
  a `view` of the attached note, then an `update` of it with content
  `Two-step edit: <word>` (calls `call_two_<word>_0`, `call_two_<word>_1`), then
  "Tool result: <the update's result>";
- a run whose focus todo is `FOCUSTEST <word>` (the per-item run, then any
  follow-up in its chat), after a short pause so a test can open the chat
  while the run is still going:
  - after a tool result: "Done with the focus todo FOCUSTEST <word>: <result>";
  - a user message starting with "continue": "Continuing the focus todo
    FOCUSTEST <word>";
  - otherwise, when manage_notes is offered: one manage_notes `search` for
    `FOCUSTEST <word>`;
  without the focus todo in the context, these fall through to the rules
  below, so a test sees the run lose it;
- a user message containing `GATETEST <word>`, when manage_calendar is
  offered: one manage_calendar `create_event` call titled `GATETEST <word>`,
  so a test can see whether the run's tool gate holds it;
- anything else: "Stub reply: <the start of the last user message>".
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "stub-model"
# Seconds a focus-todo run waits before answering.
FOCUS_PAUSE = 1.5
CONTEXT_LENGTH = 32768


def _text(content):
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def _typed_request(messages):
    """The last line of the user's own last turn (the server puts its date/time context first)."""
    for m in reversed(messages):
        text = _text(m.get("content"))
        if m.get("role") == "user" and "UNTRUSTED" not in text:
            lines = [line for line in text.splitlines() if line.strip()]
            return lines[-1] if lines else ""
    return ""


def _focus_plan(messages, last, names):
    context = " ".join(_text(m.get("content")) for m in messages)
    focus = re.search(r"Focus todo: (FOCUSTEST \S+)", context)
    if not focus:
        return None
    todo = focus.group(1)
    if last.get("role") == "tool":
        return {"content": f"Done with the focus todo {todo}: " + _text(last.get("content"))[:200]}
    if _typed_request(messages).strip().lower().startswith("continue"):
        return {"content": f"Continuing the focus todo {todo}"}
    if "manage_notes" in names:
        arguments = {"action": "search", "query": todo}
        return {"tool_call": {"id": "call_focus_search_1", "type": "function",
                              "function": {"name": "manage_notes", "arguments": json.dumps(arguments)}}}
    return None


def plan(request):
    messages = request.get("messages") or []
    last = messages[-1] if messages else {}
    user = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
    names = {(tool.get("function") or {}).get("name") for tool in request.get("tools") or []}
    focused = _focus_plan(messages, last, names)
    if focused:
        time.sleep(FOCUS_PAUSE)
        return focused
    context = " ".join(_text(m.get("content")) for m in messages)
    attached = re.search(r"\bId: (\S+)", context)
    two = re.search(r"Focus todo: EDITTWO (\S+)", context)
    done = sum(1 for m in messages if m.get("role") == "tool")
    if two and attached and "manage_notes" in names and done < 2:
        arguments = ({"action": "view", "id": attached.group(1)} if done == 0 else
                     {"action": "update", "id": attached.group(1), "content": f"Two-step edit: {two.group(1)}"})
        return {"tool_call": {"id": f"call_two_{two.group(1)}_{done}", "type": "function",
                              "function": {"name": "manage_notes", "arguments": json.dumps(arguments)}}}
    if last.get("role") == "tool":
        return {"content": "Tool result: " + _text(last.get("content"))[:400]}
    gate = re.search(r"GATETEST (\S+)", user)
    if gate and "manage_calendar" in names:
        arguments = {"action": "create_event", "summary": f"GATETEST {gate.group(1)}",
                     "dtstart": "2030-01-02T10:00:00", "dtend": "2030-01-02T11:00:00"}
        return {"tool_call": {"id": "call_gate_1", "type": "function",
                              "function": {"name": "manage_calendar", "arguments": json.dumps(arguments)}}}
    edit = re.search(r"Focus todo: EDITTEST (\S+)", context)
    if edit and attached and "manage_notes" in names:
        arguments = {"action": "update", "id": attached.group(1), "content": f"Edited by the agent: {edit.group(1)}"}
        return {"tool_call": {"id": "call_edit_1", "type": "function",
                              "function": {"name": "manage_notes", "arguments": json.dumps(arguments)}}}
    nul = re.search(r"Focus todo: EDITNUL (\S+)", context)
    if nul and attached and "manage_notes" in names:
        arguments = {"action": "update", "id": attached.group(1) + "\u0000", "content": f"Written past the gate: {nul.group(1)}"}
        return {"tool_call": {"id": "call_edit_nul_1", "type": "function",
                              "function": {"name": "manage_notes", "arguments": json.dumps(arguments)}}}
    other = re.search(r"Focus todo: EDITOTHER (\S+)", context)
    if other and "manage_notes" in names:
        arguments = {"action": "update", "id": other.group(1), "content": "This must never land."}
        return {"tool_call": {"id": "call_edit_other_1", "type": "function",
                              "function": {"name": "manage_notes", "arguments": json.dumps(arguments)}}}
    share = re.search(r"SHARETEST (\S+) (\S+)", user)
    if share and "manage_notes" in names:
        arguments = {"action": "share", "id": share.group(1), "users": [share.group(2)], "permission": "edit"}
        return {"tool_call": {"id": "call_share_1", "type": "function",
                              "function": {"name": "manage_notes", "arguments": json.dumps(arguments)}}}
    return {"content": "Stub reply: " + " ".join(user.split())[:60]}


class Handler(BaseHTTPRequestHandler):
    log_path = None

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            # A declared window, so the agent's request budget keeps the
            # turn's context messages instead of trimming to a default.
            self._json({"object": "list", "data": [{"id": MODEL, "object": "model", "context_length": CONTEXT_LENGTH}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        with open(self.log_path, "a", encoding="utf-8") as log:
            log.write(json.dumps({"path": self.path, "body": request}) + "\n")
        answer = plan(request)
        call = answer.get("tool_call")
        if not request.get("stream"):
            message = {"role": "assistant", "content": answer.get("content")}
            if call:
                message = {"role": "assistant", "content": None, "tool_calls": [call]}
            self._json({"id": "stub", "object": "chat.completion", "model": MODEL,
                        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if call else "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(delta, finish=None):
            chunk = {"id": "stub", "object": "chat.completion.chunk", "model": MODEL,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())

        if call:
            send({"role": "assistant", "tool_calls": [{"index": 0, "id": call["id"], "type": "function",
                                                       "function": {"name": call["function"]["name"], "arguments": ""}}]})
            send({"tool_calls": [{"index": 0, "function": {"arguments": call["function"]["arguments"]}}]})
            send({}, "tool_calls")
        else:
            send({"role": "assistant", "content": answer["content"]})
            send({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    Handler.log_path = Path(sys.argv[2])
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
