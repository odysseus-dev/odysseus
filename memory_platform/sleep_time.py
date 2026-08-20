"""sleep_time.py — Odysseus-native memory consolidation.

Runs after sessions to keep memory automatic:
  1. Extract recent session transcripts from Odysseus DB
  2. Mine transcripts into the hybrid store
  3. Run local reflect (direct Ollama) -> insights
  4. Write daily reflection markdown
  5. Consolidate duplicates, decay stale entries

Designed for the TaskScheduler — triggered by `consolidate_memory` action
or on a cron schedule. Uses Odysseus's DATA_DIR for all paths.

Usage (standalone):
  python sleep_time.py                    # process last 24h
  python sleep_time.py --hours 48         # process last 48h
  python sleep_time.py --session ID       # process one session
  python sleep_time.py --dry-run          # show what would happen

Usage (from TaskScheduler):
  Called via action_consolidate_memory() in builtin_actions.py.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# Odysseus-native path resolution.
try:
    from . import memory_env
except ImportError:
    import memory_env

MEM_DIR = memory_env.memory_dir()
REFLECT_DIR = memory_env.reflect_dir()
TRANSCRIPTS_DIR = memory_env.transcripts_dir()
STATUS_FILE = memory_env.status_file()
OLLAMA_URL = memory_env.ollama_url()

# Configurable model — no hardcoded persona or model preferences.
# Users configure this via settings (sleep_model key).
DEFAULT_REFLECT_MODEL = "qwen2.5:3b"

os.makedirs(REFLECT_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)


def _get_reflect_model():
    """Get the configured reflect model from settings.

    Falls back to DEFAULT_REFLECT_MODEL if not configured.
    No user-specific or persona-specific defaults.
    """
    try:
        from src.settings import get_setting
        return get_setting("sleep_model", DEFAULT_REFLECT_MODEL)
    except Exception:
        return DEFAULT_REFLECT_MODEL


def _get_sleep_enabled():
    """Check if sleep-time is enabled in settings."""
    try:
        from src.settings import get_setting
        return get_setting("sleep_enabled", True)
    except Exception:
        return True


def reflect(query, context="", model=None):
    """Run a reflection against the local Ollama model directly.

    Uses urllib instead of subprocess+curl — no shell overhead, no model
    loading delay. Returns plain text.

    Ported from the main branch fix: direct urllib.request API calls,
    configurable model, 120s timeout.
    """
    if model is None:
        model = _get_reflect_model()
    prompt = query
    if context:
        prompt = f"{query}\n\nHere is the material to reflect on:\n\n{context}"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.3, "num_predict": 900},
    }
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        return data.get("response", "")[:800]
    except Exception as e:
        return f"[reflect failed: {e}]"


def fetch_recent_sessions(hours=24):
    """Fetch recent sessions from Odysseus's database.

    Uses SQLAlchemy Session model — owner-scoped, no raw SQL.
    Returns list of (session_id, title, messages) tuples.
    """
    try:
        from core.database import SessionLocal, Session as DbSession, ChatMessage
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        db = SessionLocal()
        try:
            sessions = db.query(DbSession).filter(
                DbSession.last_accessed >= cutoff,
                DbSession.archived == False,
            ).order_by(DbSession.last_accessed.desc()).all()

            result = []
            for sess in sessions:
                messages = db.query(ChatMessage).filter(
                    ChatMessage.session_id == sess.id
                ).order_by(ChatMessage.timestamp.asc()).all()

                msg_list = []
                for m in messages:
                    try:
                        data = json.loads(m.data) if isinstance(m.data, str) else m.data
                        msg_list.append({
                            "role": data.get("role", "unknown"),
                            "content": data.get("content", ""),
                        })
                    except (json.JSONDecodeError, TypeError):
                        continue

                if msg_list:
                    result.append((sess.id, sess.name or "Untitled", msg_list))

            return result
        finally:
            db.close()
    except Exception as e:
        print(f"Warning: could not fetch sessions: {e}", file=sys.stderr)
        return []


def extract_insights(messages, model=None):
    """Extract insights from a session's messages using local LLM.

    Returns a dict with: topics, facts, patterns, questions.
    No persona-specific prompts — generic reflection.
    """
    # Build a summary of the conversation.
    summary_parts = []
    for msg in messages[-20:]:  # Last 20 messages max
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:500]
        if content:
            summary_parts.append(f"{role}: {content}")

    if not summary_parts:
        return None

    context = "\n".join(summary_parts)
    query = (
        "Analyze this conversation and extract:\n"
        "1. Key topics discussed\n"
        "2. Important facts or decisions\n"
        "3. Recurring patterns or themes\n"
        "4. Open questions or unresolved items\n\n"
        "Be concise. Format as JSON: "
        '{"topics": [...], "facts": [...], "patterns": [...], "questions": [...]}'
    )

    response = reflect(query, context=context, model=model)
    if response.startswith("[reflect failed"):
        return None

    # Try to parse JSON from response.
    try:
        # Find JSON in response.
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError:
        pass

    return {"raw": response}


def write_reflection(session_id, title, insights):
    """Write a reflection markdown file."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    filename = f"{ts}_{session_id[:8]}.md"
    filepath = os.path.join(REFLECT_DIR, filename)

    content = f"# Reflection: {title}\n\n"
    content += f"Session: {session_id}\n"
    content += f"Time: {datetime.now(timezone.utc).isoformat()}\n\n"

    if isinstance(insights, dict) and "raw" not in insights:
        if insights.get("topics"):
            content += "## Topics\n"
            for t in insights["topics"]:
                content += f"- {t}\n"
            content += "\n"
        if insights.get("facts"):
            content += "## Facts\n"
            for f in insights["facts"]:
                content += f"- {f}\n"
            content += "\n"
        if insights.get("patterns"):
            content += "## Patterns\n"
            for p in insights["patterns"]:
                content += f"- {p}\n"
            content += "\n"
        if insights.get("questions"):
            content += "## Open Questions\n"
            for q in insights["questions"]:
                content += f"- {q}\n"
            content += "\n"
    else:
        content += "## Raw Reflection\n\n"
        content += str(insights) + "\n"

    with open(filepath, "w") as f:
        f.write(content)

    return filepath


def update_status(status):
    """Update the sleep-time status file."""
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception:
        pass


def run_sleep_cycle(hours=24, session_id=None, dry_run=False):
    """Run the full sleep-time consolidation cycle.

    1. Fetch recent sessions
    2. Extract insights from each
    3. Write reflections
    4. Update status
    """
    if not _get_sleep_enabled():
        print("Sleep-time is disabled in settings.")
        return {"status": "disabled"}

    model = _get_reflect_model()
    print(f"Sleep-time: using model={model}, hours={hours}, dry_run={dry_run}")

    # Fetch sessions.
    if session_id:
        sessions = fetch_recent_sessions(hours=168)  # Wide window for specific session
        sessions = [(sid, title, msgs) for sid, title, msgs in sessions
                    if sid.startswith(session_id)]
    else:
        sessions = fetch_recent_sessions(hours=hours)

    if not sessions:
        print("No recent sessions to process.")
        update_status({"last_run": datetime.now(timezone.utc).isoformat(),
                       "sessions_processed": 0, "status": "no_sessions"})
        return {"status": "no_sessions"}

    print(f"Found {len(sessions)} session(s) to process.")

    results = []
    for sid, title, messages in sessions:
        print(f"  Processing: {title} ({len(messages)} messages)")

        if dry_run:
            print(f"    [dry-run] Would extract insights from {len(messages)} messages")
            results.append({"session": sid, "title": title, "dry_run": True})
            continue

        # Extract insights.
        insights = extract_insights(messages, model=model)
        if insights:
            filepath = write_reflection(sid, title, insights)
            print(f"    Wrote reflection: {filepath}")
            results.append({"session": sid, "title": title,
                           "insights": insights, "file": filepath})
        else:
            print(f"    No insights extracted (model unavailable or empty session)")
            results.append({"session": sid, "title": title, "insights": None})

    # Update status.
    status = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "sessions_processed": len(sessions),
        "model": model,
        "results": results,
    }
    update_status(status)

    print(f"Sleep-time complete: {len(sessions)} session(s) processed.")
    return status


def main():
    parser = argparse.ArgumentParser(description="Sleep-time memory consolidation")
    parser.add_argument("--hours", type=int, default=24,
                        help="Process sessions from last N hours")
    parser.add_argument("--session", type=str, default=None,
                        help="Process a specific session ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without writing")
    args = parser.parse_args()

    run_sleep_cycle(hours=args.hours, session_id=args.session,
                    dry_run=args.dry_run)


if __name__ == "__main__":
    main()
