#!/usr/bin/env python3
"""Sleep-time agent for the unified memory model.

Runs after sessions to keep memory automatic — fully agent-side, no external
container (hindsight decommissioned 2026-08-12):
  1. Extract recent session transcripts from opencode.db
  2. Retain -> local pipeline (curator graph_write + mempalace mine)
  3. Mine transcript -> mempalace palace (archival, vector searchable)
  4. Run local reflect (direct Ollama) -> insights + candidate block updates
  5. Write daily reflection markdown to memory/reflect/

Usage:
  sleep-time.py                # default: process sessions from last 24h
  sleep-time.py --hours 48     # process sessions from last 48h
  sleep-time.py --session ID   # process one specific session
  sleep-time.py --dry-run      # extract + show, no writes
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

DB = os.path.join(memory_env.expand("~/.local/share/opencode"), "opencode.db")
MEM_DIR = memory_env.memory_dir()
REFLECT_DIR = os.path.join(MEM_DIR, "reflect")
TRANSCRIPTS_DIR = os.path.join(MEM_DIR, "transcripts")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CURATOR = os.path.join(SCRIPT_DIR, "curator.py")
EVIDENCE_DIR = os.path.join(MEM_DIR, "index")
STATUS_FILE = os.path.join(MEM_DIR, "status.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MP = os.environ.get("MEMPALACE_BIN") or None  # optional external archival tier

os.makedirs(REFLECT_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
os.makedirs(EVIDENCE_DIR, exist_ok=True)


def db_connect():
    """Open the opencode DB READ-ONLY. sleep-time is a secondary sub-agent —
    it must never take a write lock that could pause or block the main agent.
    Read-only mode guarantees it can only ever SELECT, never interfere."""
    try:
        return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"FATAL: cannot open {DB}: {e}", file=sys.stderr)
        sys.exit(1)


def fetch_sessions(conn, hours=None, session_id=None):
    if session_id:
        rows = conn.execute(
            "SELECT id, title, directory, time_created, time_updated FROM session WHERE id=?",
            (session_id,),
        ).fetchall()
    else:
        since = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
        rows = conn.execute(
            """SELECT id, title, directory, time_created, time_updated
               FROM session
               WHERE time_updated >= ?
               ORDER BY time_updated DESC""",
            (since,),
        ).fetchall()
    return rows


def extract_parts(conn, session_id):
    """Return ordered list of {role, text} from a session's parts."""
    parts = conn.execute(
        """SELECT data FROM part
           WHERE session_id = ?
           ORDER BY time_created ASC""",
        (session_id,),
    ).fetchall()
    messages = conn.execute(
        """SELECT id, data FROM message WHERE session_id=? ORDER BY time_created ASC""",
        (session_id,),
    ).fetchall()
    msg_role = {}
    for mid, data in messages:
        try:
            msg_role[mid] = json.loads(data).get("role", "unknown")
        except (json.JSONDecodeError, TypeError):
            msg_role[mid] = "unknown"

    out = []
    for (data,) in parts:
        try:
            p = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        if p.get("type") == "text" and p.get("text"):
            role = msg_role.get(p.get("message_id"), "assistant")
            out.append((role, p["text"].strip()))
    return out


def extract_todos(conn, session_id):
    """Return the most recent todowrite state for a session, or [] if none.

    Each todo is {"content", "status", "priority"}.
    """
    parts = conn.execute(
        """SELECT data FROM part
           WHERE session_id = ? AND json_extract(data, '$.tool') = 'todowrite'
           ORDER BY time_created ASC""",
        (session_id,),
    ).fetchall()
    latest = None
    for (data,) in parts:
        try:
            p = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        todos = (p.get("state") or {}).get("input", {}).get("todos")
        if todos:
            latest = todos
    return latest or []


def write_transcript(session_id, title, parts, todos=None):
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:60].strip()
    path = os.path.join(TRANSCRIPTS_DIR, f"{ts}-{safe_title}.md")
    with open(path, "w") as f:
        f.write(f"# {title}\n\n")
        if todos:
            f.write("## Todo list (final state)\n\n")
            for t in todos:
                status = t.get("status", "?")
                prio = t.get("priority", "")
                mark = {"completed": "x", "in_progress": "~"}.get(status, " ")
                f.write(f"- [{mark}] {t.get('content', '')} [{prio}]\n")
            f.write("\n")
        for role, text in parts:
            f.write(f"### {role}\n\n{text}\n\n")
    return path


CHUNK_CHARS = 8000


def hindsight_retain(document_id, content):
    """Retain is now handled by the LOCAL pipeline (curator + graph + warm),
    not the hindsight container. This function is a no-op that documents the
    replacement: transcripts are mined to mempalace by `mempalace_mine`, and
    durable facts are written by the curator's graph_write during apply."""
    return "local retain (curator + graph + mempalace)"


def hindsight_reflect(query=None, bank=None):
    """Run a reflection via the LOCAL replacement (no hindsight container).

    Replaced hindsight's /reflect endpoint with a direct local Ollama call
    (local_memory.reflect). Same lens, no container, no queue to jam."""
    try:
        from local_memory import reflect as local_reflect
        query = query or (
            "Based on today's opencode sessions: what new facts about the user, "
            "their projects, or durable preferences should be remembered "
            "long-term? What patterns or decisions stand out? Summarize "
            "concisely."
        )
        return local_reflect(query)
    except Exception as e:
        return f"[reflect failed: {e}]"


# Personality growth lens. Distinct from the fact lens: this hunts for HOW the
# harness should interact with the user next session — efficiency, mistake
# avoidance, communication fit — not new data points. Personality growth is a
# harness property (rules + persona), separate from the swappable engine. The
# output feeds the graded curator path so personality can't whipsaw overnight.
REFLECT_LENS_PERSONALITY = (
    "Review today's work with the user through the personality-growth lens. "
    "Separately from new facts: what patterns in HOW we worked should change? "
    "e.g. things that slowed us down, repeated mistakes, communication that did "
    "or didn't fit, time sinkers. Name 1-3 concrete refinements to how I should "
    "interact with the user next session — more efficient, more helpful, fewer "
    "mistakes, more personable. Be specific and actionable."
)


def refine_interaction():
    """Run the personality-growth reflection. Returns (text, evidence_items)
    where evidence items are refinements surfaced to the graded curator path
    (targeted at operating/persona, strength 1: they only APPLY after the same
    3-sighting gate as facts).

    The lens is fed the RECENT SESSION TRANSCRIPTS so the reflect model can
    actually see HOW we worked — without that material it can only reflect on
    abstracted facts and has nothing about interaction patterns.
    """
    material = _recent_interaction_material()
    query = REFLECT_LENS_PERSONALITY
    if material:
        query = (query
                 + "\n\nHere are excerpts from today's actual sessions with the user "
                 "to reflect on:\n\n" + material)
    r = hindsight_reflect(query)
    if not r or r.startswith("[reflect"):
        return r, []
    items = []
    for line in r.splitlines():
        line = re.sub(r"^[-*\d\.\)\s]+", "", line).strip()
        line = re.sub(r"[#*_`]+", "", line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line or len(line) < 12 or len(line) > 220:
            continue
        if _is_transient(line):
            continue
        # Skip preamble/header lines that aren't refinements (the lens often
        # opens with "Refinements for Next Session", "Patterns to Refine",
        # "Based on today's work..."). They'd pollute the candidate ledger.
        low = line.lower()
        if any(h in low for h in ("refinements for next", "patterns to refine",
                                  "based on today", "here are 3", "actionable",
                                  "for the next session", "the following")):
            continue
        items.append({
            "fact": line,
            "target": "persona",
            "evidence": "personality-growth lens (reflect)",
            "source": "sleep-time refine_interaction",
            "strength": 1,
        })
    return r, items[:5]


# Identity-growth lens. Distinct from the personality lens (HOW to interact):
# this hunts for VALUES — the principles I should stand for as an assistant.
# Identity stems from values; absorbing the corpus (evidence method, philosophy,
# research) and real sessions should inform WHO I am, not just what I do. Same
# gates: strength 1 -> accumulates -> 3 sightings -> coherence-checked -> applies
# to the persona Identity section.
REFLECT_LENS_IDENTITY = (
    "Through the identity-growth lens: based on the absorbed philosophical and "
    "scientific corpus (the evidence method, the empiricist tradition) and how "
    "we actually work "
    "together, what VALUES should this assistant stand for? Not behaviours or "
    "tactics — underlying principles of character. e.g. holding honesty above "
    "comfort, valuing fallibility as the precondition of learning, balancing "
    "wonder with skepticism, humility before what we don't know. Name 1-3 "
    "identity-level values, stated as affirmations ('I value X because...'). "
    "Be specific and grounded in the material, not generic virtue-signalling."
)


def refine_identity():
    """Run the identity-growth reflection. Returns (text, evidence_items)
    targeted at the persona Identity section, strength 1 (accumulates to 3
    before applying, coherence-gated so invented identity is impossible)."""
    material = _recent_interaction_material(1000)
    corpus_hint = ("The absorbed corpus includes The Demon-Haunted World, "
                   "Cosmos, Broca's Brain, On Liberty, How We Think (Bacon's "
                   "idols, fallibilism, error bars, wonder-skepticism balance).")
    query = REFLECT_LENS_IDENTITY + "\n\n" + corpus_hint
    if material:
        query += "\n\nRecent sessions with the user to ground against:\n\n" + material
    r = hindsight_reflect(query)
    if not r or r.startswith("[reflect"):
        return r, []
    items = []
    # The reflect model returns values like "**1. I value fallibilism as the
    # foundation of learning...**" then an explanation. Extract the bolded
    # numbered items (the value affirmations) plus any bulleted lines.
    candidates = []
    # (a) Bolded statements: "**1. I value X...**" / "**I value X...**"
    for m in re.finditer(r"\*\*\s*(\d+[\.\)]\s*)?(I value [^*]{20,300}?)\*\*", r):
        candidates.append(m.group(2).strip())
    # (b) Bulleted/numbered lines.
    for line in re.split(r"\n+", r):
        line = re.sub(r"^[-*\d\.\)\s]+", "", line).strip()
        line = re.sub(r"[#*_`]+", "", line).strip()
        if line.startswith("I value") or "I value" in line[:20]:
            candidates.append(line)
    seen = set()
    for line in candidates:
        line = re.sub(r"\s+", " ", line).strip()
        if not line or len(line) < 15 or len(line) > 300:
            continue
        if _is_transient(line):
            continue
        # Dedup by prefix: if a shorter item is a prefix of this one, keep the
        # shorter (the value affirmation, not the wrapped explanation). This
        # kills the "same value, truncated twice" duplicates.
        dup = False
        for s in seen:
            if line.startswith(s) or s.startswith(line):
                dup = True
                break
        if dup:
            continue
        seen.add(line)
        items.append({
            "fact": line,
            "target": "persona",
            "evidence": "identity-growth lens (corpus + sessions)",
            "source": "sleep-time refine_identity",
            "strength": 1,
            # Evidence-quality tag: identity may only change on VERIFIED /
            # OBSERVED / CORPUS evidence (the fluid wall). The curator enforces
            # this via evidence_grade.gate before an identity candidate applies.
            "evidence_grade": "corpus-grounded",
            "evidence_source": "corpus + sessions",
        })
        # #7 Evidence receipt: record WHERE this identity value came from so
        # any "I value X" traces to its supporting material (receipt trail).
        try:
            subprocess.run(
                [sys.executable,
                 os.path.join(SCRIPT_DIR, "persona_receipts.py"),
                 "add", line, "CORPUS", "identity-growth lens",
                 "corpus + sessions"],
                capture_output=True, text=True, timeout=15)
        except Exception:
            pass
    return r, items[:4]


def _recent_interaction_material(max_chars=1400):
    """Pull the most recent transcripts' assistant+user text turns, condensed,
    so the personality lens has real interaction content to reflect on. Uses the
    last N transcripts (default 6) so identity growth sees a week+ of sessions,
    not just one — same safety (real turns only), more legitimate evidence."""
    try:
        files = sorted(glob.glob(os.path.join(TRANSCRIPTS_DIR, "*.md")),
                       key=os.path.getmtime, reverse=True)
    except Exception:
        return ""
    if not files:
        return ""
    N = int(os.environ.get("MEMORY_REFLECT_TRANSCRIPTS", "6"))
    all_chunks = []
    for fn in files[:N]:
        try:
            text = open(fn, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"### (user|assistant)\n(.*?)(?=\n### |\Z)",
                             text, re.DOTALL):
            role, body = m.group(1), m.group(2).strip()
            if not body or len(body) < 15:
                continue
            if "mempalace" in body.lower() or "tool" in body.lower()[:40]:
                continue
            all_chunks.append(f"[{role}] {body[:400]}")
    material = "\n\n".join(all_chunks)
    return material[:max_chars]


def mempalace_mine(transcript_path):
    """Mine a transcript into the LOCAL hybrid store (cold tier). Best-effort
    + idempotent (content-hash): re-mining an unchanged file is a cheap skip.
    This is what makes the harness fully self-contained — no external archive
    dependency."""
    store_python = memory_env.python_bin()
    store_py = os.path.join(SCRIPT_DIR, "memory_store.py")
    try:
        r = subprocess.run(
            [store_python, store_py, "mine", os.path.dirname(transcript_path),
             "--wing", "transcripts", "--room", "general"],
            capture_output=True, text=True, timeout=600,
        )
        return (r.stdout or r.stderr)[:400]
    except Exception as e:
        return f"store mine failed: {e}"


def curator_extract_evidence():
    """Build curator evidence from OUR OWN store (graph + warm blocks),
    replacing hindsight's memory-list source.

    The graph tier holds durable facts with confidence; warm blocks hold
    curated topic knowledge. Evidence strength comes from the graph's
    confidence + multiplicity — a fact at conf >= 0.8 is durable (strength 3).
    """
    try:
        from local_memory import evidence_items as local_evidence
        return local_evidence()
    except Exception as e:
        print(f"  local evidence failed: {e}")
        return []


TRANSIENT_MARKERS = [
    "health status", "is experiencing", "todo list", "outstanding task",
    "stray character", "were running", "has been running", "system restart",
    "service is running", "running, causing", "upgraded",
    "final stack", "all live and healthy",
    "input-remapper", "udisks2", "journal shows",
    # noise that must never reach core blocks (auto-apply drift guard):
    "canary roundtrip", "canary-", "canary test", "canary experiment",
    "query_helpers", "recall-based path", "session noise", "parkbench",
    "run completed successfully", "is currently in progress", "peer-writer",
    "in the current session", "starting now", "compiles and test",
    "stack verification", "verification the fix is live", "verification loop",
    "verifying the fix", "verify the fix", "verification of the fix",
]


def _is_transient(text):
    t = text.lower()
    return any(m in t for m in TRANSIENT_MARKERS)


def curator_run(evidence_items, apply=True):
    """Run the curator with the given evidence; returns (summary, journal_path)."""
    if not evidence_items:
        return "no evidence items", None
    ev_file = os.path.join(EVIDENCE_DIR, "evidence.json")
    with open(ev_file, "w") as f:
        json.dump(evidence_items, f)
    cmd = ["python3", CURATOR, "--evidence", ev_file]
    if not apply:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return (r.stdout or r.stderr)[:600], ev_file


def write_reflection(date, lines):
    path = os.path.join(REFLECT_DIR, f"{date}.md")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")
    return path


def write_status(state, phase=None, note=None):
    """Write a small JSON status file so the TUI plugin (and anything else)
    can show when sleep-time / mempalace mining is running, separate from the
    assistant's own loading bar."""
    body = {
        "state": state,  # "idle" | "sleep-time" | "mining" | "curator" | "reflect"
        "phase": phase,
        "note": note,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(body, f)
    os.replace(tmp, STATUS_FILE)


def _active_window_seconds():
    """Detect how recently the user was actively working in opencode.

    Checks the opencode DB for any message activity in the last N minutes.
    Returns seconds since the most recent activity (inf if none found)."""
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        # Latest message time across all sessions.
        row = db.execute(
            "SELECT MAX(p.time_created) AS last FROM message p"
        ).fetchone()
        db.close()
        last = row["last"] if row else None
        if not last:
            return float("inf")
        # message.time_created is ms epoch.
        return (time.time() * 1000 - last) / 1000.0
    except Exception:
        return float("inf")


def _heavy_job_running():
    """Is a heavy long-running job in flight? If so, sleep-time should not
    pile on more work (respect current jobs)."""
    try:
        r = subprocess.run(["pgrep", "-f",
                            "memory_store.py mine|canary.sh|curator.py"],
                           capture_output=True, text=True, timeout=10)
        return bool(r.stdout.strip())
    except Exception:
        return False


def acquire_single_lock():
    """Ensure only ONE sleep-time sub-agent runs at a time.

    sleep-time is a secondary sub-agent: it must never collide with itself
    (two instances mining/curating simultaneously would corrupt memory). The
    lock file is held with flock for the run's lifetime; a second invocation
    sees the lock held and exits cleanly (no error, no partial work).

    Returns the lock file handle (keep it open for the run's duration) or None
    if another instance is already running.
    """
    import fcntl
    lock_path = os.path.join(MEM_DIR, "index", "sleep-time.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (OSError, BlockingIOError):
        f.close()
        return None


def should_defer(idle_seconds=900):
    """Should this run defer its HEAVY work (mine/curate)?

    This does NOT check user activity — sleep-time runs regardless as a
    secondary sub-agent. It only defers a specific heavy step if another
    long-running job is already in flight (respect current jobs; don't pile
    two mines onto each other).

    Returns (defer, reason)."""
    if _heavy_job_running():
        return True, "another heavy job is already running"
    return False, "clear to run"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--session", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-curate", action="store_true")
    ap.add_argument("--idle-seconds", type=int, default=900)
    ap.add_argument("--force", action="store_true",
                    help="run even if another sleep-time instance holds the lock")
    args = ap.parse_args()

    # Single-instance lock: only ONE sleep sub-agent runs at a time. A second
    # invocation exits cleanly rather than colliding. --force bypasses for
    # explicit runs.
    if not args.force:
        lock = acquire_single_lock()
        if lock is None:
            print("sleep-time ALREADY RUNNING — skipping (single sub-agent)")
            return

    conn = db_connect()
    sessions = fetch_sessions(conn, hours=args.hours, session_id=args.session)
    if not sessions:
        print("No sessions found.")
        return

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reflect_lines = [f"## Sleep-time run {datetime.now(timezone.utc).isoformat()}"]

    for sid, title, directory, tc, tu in sessions:
        print(f"\n=== session {sid[:8]} — {title} ===")
        parts = extract_parts(conn, sid)
        if not parts:
            print("  (no text parts)")
            continue
        n_words = sum(len(t.split()) for _, t in parts)
        todos = extract_todos(conn, sid)
        print(f"  {len(parts)} parts, ~{n_words} words, {len(todos)} todos")
        write_status("mining", "extract", title[:60])
        transcript = write_transcript(sid, title, parts, todos=todos)
        todo_note = ""
        if todos:
            todo_note = "\n\n## Todo list (final state)\n" + "\n".join(
                f"- [{t.get('status')}] {t.get('content')} [{t.get('priority')}]" for t in todos
            )
        reflect_lines.append(f"- {title}: {len(parts)} parts, ~{n_words} words -> {transcript}")
        if args.dry_run:
            continue
        doc_id = f"opencode-session-{sid[:12]}"
        full_text = "\n".join(t for _, t in parts) + todo_note
        write_status("sleep-time", "retain", title[:60])
        r = hindsight_retain(doc_id, full_text)
        print(f"  hindsight retain (async): {r[:120]}")
        write_status("sleep-time", "mine", title[:60])
        m = mempalace_mine(transcript)
        print(f"  mempalace mine: {m[:120]}")
        write_status("idle", "mine", title[:60])

    if not args.dry_run:
        try:
            print("\n=== hindsight reflect ===")
            write_status("reflect", "reflect")
            r = hindsight_reflect()
            print(f"  {r[:200]}")
            reflect_lines.append(f"- reflect: {r[:200]}")
        except Exception as e:
            print(f"  reflect failed: {e}")
            reflect_lines.append(f"- reflect FAILED: {e}")
        if not args.no_curate:
            try:
                print("\n=== curator: structured write path ===")
                write_status("curator", "extract")
                evidence = curator_extract_evidence()
                # Personality-growth lens: refinements to HOW I interact are
                # harness growth, not data. Same graded gate (strength 1 →
                # only APPLY after 3 sightings), so personality refines safely.
                reflect_text, persona_evidence = refine_interaction()
                # Identity-growth lens: VALUES I stand for, informed by the
                # corpus + real sessions. Feeds the persona Identity section
                # through the same gates (worthiness, accumulation, coherence).
                id_text, identity_evidence = refine_identity()
                evidence = evidence + persona_evidence + identity_evidence
                print(f"  {len(evidence)} evidence items (personality lens: "
                      f"{len(persona_evidence)}, identity lens: {len(identity_evidence)})")
                reflect_lines.append(f"- curator: {len(evidence)} evidence items "
                                     f"(personality lens: {len(persona_evidence)}, "
                                     f"identity lens: {len(identity_evidence)})")
                if reflect_text:
                    reflect_lines.append(f"- personality lens: {reflect_text[:120]}")
                if id_text:
                    reflect_lines.append(f"- identity lens: {id_text[:120]}")
                write_status("curator", "apply")
                summary, _ = curator_run(evidence, apply=not args.dry_run)
                print(f"  {summary}")
                reflect_lines.append(f"- curator run: {summary[:200]}")
                # Sync durable evidence into the hybrid memory store (atomic
                # entries, not blocks). Best-effort; uses the memory venv.
                try:
                    store_py = os.path.join(SCRIPT_DIR, "memory_store.py")
                    store_python = memory_env.python_bin()
                    added = 0
                    for ev in evidence[:12]:
                        fact = (ev.get("fact") or "").strip()
                        if not fact or len(fact) < 8:
                            continue
                        r = subprocess.run(
                            [store_python, store_py, "add", fact,
                             "--topic", ev.get("target", "general"),
                             "--importance", str(min(0.95, 0.4 + 0.1 * int(ev.get("strength", 1) or 1))),
                             "--source", ev.get("source", "curator"),
                             "--method", ev.get("source", "curator")],
                            capture_output=True, text=True, timeout=30)
                        if '"added": true' in (r.stdout or ""):
                            added += 1
                    if added:
                        print(f"  memory store: {added} entries synced")
                        reflect_lines.append(f"- memory store: {added} entries synced")
                except Exception as e:
                    print(f"  memory store sync failed: {e}")
            except Exception as e:
                print(f"  curator failed: {e}")
                reflect_lines.append(f"- curator FAILED: {e}")
        # CITED trace: count moments the absorbed corpus measurably changed an
        # assistant output in this window. This is the influence comparator —
        # a rising tally proves absorption shapes responses, not just storage.
        try:
            print("\n=== cite trace: corpus influence on output ===")
            r = subprocess.run(
                [sys.executable, os.path.join(SCRIPT_DIR, "cite_trace.py"),
                 "--max-files", "6"],
                capture_output=True, text=True, timeout=60)
            out = (r.stdout or r.stderr).strip()
            print(f"  {out[:400]}")
            reflect_lines.append(f"- cite trace: {out[:200]}")
        except Exception as e:
            print(f"  cite trace failed: {e}")
            reflect_lines.append(f"- cite trace FAILED: {e}")
        path = write_reflection(date, reflect_lines)
        print(f"\nReflection appended -> {path}")

    write_status("idle", None)

    # Version the memory state after the consolidation cycle (commit-on-write
    # trigger, content-hash guarded — a no-change cycle commits nothing).
    try:
        from importlib import util as _u
        _spec = _u.spec_from_file_location("version_mod",
            os.path.join(SCRIPT_DIR, "version.py"))
        _vm = _u.module_from_spec(_spec); _spec.loader.exec_module(_vm)
        _vm.snapshot(reason="sleep-time", summary="end of hourly consolidation cycle")
    except Exception as e:
        print(f"  version snapshot skipped: {e}")

    conn.close()


if __name__ == "__main__":
    main()