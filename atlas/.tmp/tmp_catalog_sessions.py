"""Build a markdown catalog of Cursor IDE agent sessions from transcript jsonl files."""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ["USERPROFILE"]) / ".cursor/projects/c-Users-tylar-code-odysseus/agent-transcripts"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tmp_session_catalog.md")


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def clean_title(text):
    m = re.search(r"<user_query>\s*(.*?)\s*(</user_query>|$)", text, re.S)
    if m:
        text = m.group(1)
    # drop other xml-ish blocks
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text[:120] if text else "(no user message)"


def clean_recap(text, limit=280):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text[:limit] if text else ""


sessions = []
for d in ROOT.iterdir():
    if not d.is_dir() or d.name == "subagents":
        continue
    f = d / f"{d.name}.jsonl"
    if not f.exists():
        continue
    first_user = None
    last_assistant = None
    n_user = 0
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message", rec)
                role = msg.get("role") or rec.get("role") or rec.get("type")
                content = msg.get("content") or rec.get("content")
                if content is None:
                    continue
                text = extract_text(content)
                if not text.strip():
                    continue
                if role == "user":
                    n_user += 1
                    if first_user is None and "<user_query>" in text:
                        first_user = text
                    elif first_user is None and not text.lstrip().startswith("<"):
                        first_user = text
                elif role == "assistant":
                    last_assistant = text
    except OSError:
        continue
    mtime = datetime.fromtimestamp(f.stat().st_mtime)
    sessions.append({
        "id": d.name,
        "mtime": mtime,
        "title": clean_title(first_user or ""),
        "recap": clean_recap(last_assistant or ""),
        "msgs": n_user,
    })

sessions.sort(key=lambda s: s["mtime"], reverse=True)

lines = []
for s in sessions:
    lines.append(f"### {s['title']}")
    lines.append(f"- **Time:** {s['mtime']:%Y-%m-%d %H:%M}")
    lines.append(f"- **Session ID:** `{s['id']}`")
    lines.append(f"- **Resume:** `agent --resume=\"{s['id']}\"`")
    if s["recap"]:
        lines.append(f"- **Recap:** {s['recap']}")
    lines.append("")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"{len(sessions)} sessions written to {OUT}")
