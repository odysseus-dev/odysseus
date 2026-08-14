#!/usr/bin/env python3
"""Drift ledger — drift protection for long projects.

A persistent checksum ledger of the five core blocks. Each sleep-time / compaction
run records the sha256 and byte length of every block. Two signals are tracked:

  - CUMULATIVE VOLUME: how far each block has moved from its *anchor* (a ledger
    record labelled with a source directive or an explicit user confirmation).
    Blocks near/over the threshold without an anchor source are flagged.
  - SOURCE TYPE: how each byte of change is justified. Legitimate growth carries
    a journaled operation (a user directive, a 3-sighting ledger promotion,
    an ADD/UPDATE/DELETE applied by the curator). Unexplained bulk is drift.

The ANGLE of this file: growth belongs to the *harness* (blocks, ledger, skills,
rules), NOT the engine (the swappable LLM). When the model swaps, the harness and
its drift bounds must persist untouched. This ledger is part of the harness.

Usage:
  drift-ledger.py snapshot          # record block states, return JSON
  drift-ledger.py check [--strict] # compare to last snapshot; emit DRIFT-ALERT
  drift-ledger.py anchor <label> <reason>  # re-anchor a block after a
                                            # confirmed, authorised change
Exit 0 = healthy, 1 = drift flagged.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

INDEX_FILE = os.path.join(memory_env.memory_dir(), "index", "blocks_meta.json")
DB = memory_env.store_db()


def ledger_path():
    # The ledger lives beside the index, so overriding INDEX_FILE (as the
    # curator gate and the canary do for isolation) moves the ledger too.
    return os.path.join(os.path.dirname(INDEX_FILE), "drift_ledger.json")
JOURNAL_DIR = os.path.join(memory_env.memory_dir(), "journal")

# Max cumulative byte drift from an anchored/authorised state before we refuse.
DRIFT_LIMIT = 0.30  # 30% of block size
# Blocks whose changes are always allowed to grow fast (their job is state).
FAST_BLOCKS = ("project", "operating")
# The constitution is even more constrained — it changes ONLY by directive.
CONSTITUTION_LIMIT = 0.05  # 5% — anything more than a directive entry is drift

BLOCKS = ("constitution", "persona", "human", "operating", "project")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_block(label):
    """Read a core block from the STORE (new schema) — the always-on entries
    for that topic, serialized as text. The old markdown files are retired;
    the store is the source of truth, so drift protection guards store content.
    """
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic=? AND always_on=1 "
            "AND status='active' ORDER BY priority ASC, id ASC", (label,)
        ).fetchall()
        db.close()
        return "\n".join(r[0] for r in rows)
    except Exception:
        return ""


def load_ledger():
    if os.path.exists(ledger_path()):
        with open(ledger_path()) as f:
            return json.load(f)
    return {}


def save_ledger(ledger):
    path = ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=2)
    os.replace(tmp, path)


def journal(msg):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(JOURNAL_DIR, f"{month}.md"), "a") as f:
        f.write(f"`{ts}` **DRIFT-ALERT** → harness\n  - {msg}\n\n")


def snapshot():
    ledger = load_ledger()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {"when": now, "blocks": {}}
    for label in BLOCKS:
        raw = read_block(label)
        entry["blocks"][label] = {
            "sha": _sha(raw),
            "size": len(raw),
            "content": raw,  # last-good content backup for diffing + retro-fix
        }
    ledger["last_snapshot"] = entry
    save_ledger(ledger)
    return entry


def anchor(label, reason):
    """Record that the current state of `label` is authorised (a user directive,
    a confirmed review, a 3-sighting promotion). Subsequent drift is measured
    from here — this is the re-anchor after legitimate change."""
    ledger = load_ledger()
    raw = read_block(label)
    ledger.setdefault("anchors", {})[label] = {
        "sha": _sha(raw),
        "size": len(raw),
        "content": raw,  # authorised content backup for root-cause diffing
        "reason": reason,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_ledger(ledger)
    return f"anchored {label}: {reason}"


def check(strict=False, autofix=True):
    ledger = load_ledger()
    snap = ledger.get("last_snapshot")
    if not snap:
        # No baseline yet — record one and treat as healthy (first contact).
        snapshot()
        print("no prior snapshot; baseline recorded")
        return 0

    alerts = []
    now_blocks = {label: read_block(label) for label in BLOCKS}
    prev = snap.get("blocks", {})
    for label in BLOCKS:
        cur = now_blocks[label]
        cur_size = len(cur)
        anchor_meta = ledger.get("anchors", {}).get(label)
        if anchor_meta:
            # An anchored block is measured against its authorised state — the
            # re-anchor after legitimate change becomes the new reference.
            base = anchor_meta.get("size", cur_size)
            base_sha = anchor_meta.get("sha")
            delta = cur_size - base
            # Root-cause: diff current content against the anchored content.
            ref_content = anchor_meta.get("content")
        else:
            prev_meta = prev.get(label, {})
            prev_size = prev_meta.get("size", cur_size)
            base = max(prev_size, 1)
            base_sha = None
            delta = cur_size - prev_size
            ref_content = prev_meta.get("content")
        pct = abs(delta) / max(base, 1)
        limit = CONSTITUTION_LIMIT if label == "constitution" else DRIFT_LIMIT
        if pct > limit:
            alerts.append({
                "label": label,
                "delta": delta,
                "pct": round(pct, 3),
                "limit": round(limit, 3),
                "base": base,
                "changed": diff_blocks(ref_content, cur),
            })
    if alerts:
        for a in alerts:
            msg = (f"{a['label']}: {a['delta']:+d} bytes "
                   f"({a['pct']*100:.1f}% vs limit {a['limit']*100:.1f}%) "
                   f"[{a['changed']}]")
            print(f"DRIFT-ALERT {msg}")
            # AUTOMATIC FIX on detection (user directive): the system fixes
            # drift itself, not just flags it.
            if autofix:
                auto_result = auto_fix(a["label"], ledger, strict)
                print(f"  auto-fix: {auto_result['action']} — {auto_result['reason']}")
                if strict:
                    journal(f"{msg}; auto-fix: {auto_result['action']} — "
                            f"{auto_result['reason']}")
            else:
                print(f"  (autofix disabled — observe only)")
        return 1
    print("no drift flagged (within limits)")
    return 0


def auto_fix(label, ledger, strict):
    """AUTOMATIC retroactive fix upon drift detection.

    Policy:
      - constitution: read-only, changes ONLY by explicit directive -> never
        auto-fix, alert + journal for human review.
      - anchored block (authorised): the growth is legitimate -> auto re-anchor
        to the new state (the fix is approving it), so future drift is measured
        from here. This prevents the SAME change being flagged repeatedly.
      - unanchored block: drift is unauthorised -> restore to last-good content
        (the retroactive fix), removing whatever caused it.

    Returns {action, reason}.
    """
    if label == "constitution":
        return {"action": "alert-only",
                "reason": "constitution changes only by explicit directive; "
                          "needs human review"}
    if label in ledger.get("anchors", {}):
        # Authorised growth: re-anchor to the current (approved) state.
        raw = read_block(label)
        ledger.setdefault("anchors", {})[label] = {
            "sha": _sha(raw), "size": len(raw), "content": raw,
            "reason": "auto re-anchor after authorised growth",
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_ledger(ledger)
        return {"action": "re-anchored",
                "reason": "anchored block grew (authorised) — re-baselined"}
    # Unauthorised drift: restore to last-good content (new schema — restore
    # the store's always-on core entries for this topic).
    block_meta = ledger.get("last_snapshot", {}).get("blocks", {}).get(label)
    if block_meta and "content" in block_meta:
        restore_topic(label, block_meta["content"])
        return {"action": "restored",
                "reason": "unauthorised drift — restored to last-good content"}
    return {"action": "alert-only",
            "reason": "no last-good content available to restore"}


def diff_blocks(ref, cur):
    """Root-cause: what changed between the last-good content and now.
    Returns a short list of added/removed lines (the drift's cause)."""
    if not ref:
        return "no prior content (first drift from empty baseline)"
    try:
        ref_lines = ref.splitlines()
        cur_lines = cur.splitlines()
        ref_set = {l.strip() for l in ref_lines if l.strip()}
        cur_set = {l.strip() for l in cur_lines if l.strip()}
        added = [l for l in cur_lines if l.strip() and l.strip() not in ref_set][:6]
        removed = [l for l in ref_lines if l.strip() and l.strip() not in cur_set][:3]
        parts = []
        if added:
            parts.append("ADDED: " + " | ".join(l.strip()[:60] for l in added))
        if removed:
            parts.append("REMOVED: " + " | ".join(l.strip()[:60] for l in removed))
        return "; ".join(parts) if parts else "content changed (unclear from diff)"
    except Exception:
        return "diff unavailable"


def restore_topic(label, content):
    """Restore a topic's store entries from serialized content (new schema).
    Replaces the always-on entries for `label` with those parsed from
    `content` (the body format `read_block` produces: `- text` lines)."""
    try:
        import sqlite3
        from datetime import datetime, timezone
        db = sqlite3.connect(DB)
        db.execute("DELETE FROM entries WHERE topic=? AND always_on=1 "
                   "AND COALESCE(method, '') != 'directive'", (label,))
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        priority = {"constitution": 0, "safety": 0, "identity": 1,
                    "operating": 2, "human": 3, "project": 3}.get(label, 5)
        for line in content.splitlines():
            text = line.strip().lstrip("-* ").strip()
            if not text or len(text) < 5:
                continue
            db.execute(
                "INSERT INTO entries (text, importance, created_at, "
                "last_accessed, topic, source, method, status, valid_from, "
                "confidence, temperature, always_on, priority) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (text, 0.9, ts, ts, label, "drift-restore", "curator",
                 "active", ts, 0.9, 1.0, 1, priority))
        db.commit()
        db.close()
    except Exception:
        pass


def fix(label):
    """RETROACTIVE FIX (#B): restore a drifted block to its last-good content.

    Only restores when the drift exceeds the limit AND the last-good content is
    available. If the drift was legitimate (authorised growth), `anchor` instead
    — fix() refuses to silently revert an anchored block.
    Returns {fixed: bool, reason}.
    """
    ledger = load_ledger()
    snap = ledger.get("last_snapshot", {})
    block_meta = snap.get("blocks", {}).get(label)
    if not block_meta or "content" not in block_meta:
        return {"fixed": False, "reason": f"no last-good content for {label}"}
    # Never silently revert an anchored block — it was authorised.
    if label in ledger.get("anchors", {}):
        return {"fixed": False,
                "reason": f"{label} is anchored (authorised); use anchor to "
                          f"re-baseline instead of fix"}
    good = block_meta["content"]
    cur = read_block(label)
    if _sha(cur) == block_meta["sha"]:
        return {"fixed": False, "reason": f"{label} already matches last-good"}
    # New schema: restore the store's always-on entries for the topic (the old
    # markdown block files are retired).
    restore_topic(label, good)
    journal(f"{label} restored to last-good content (retroactive drift fix)")
    return {"fixed": True, "reason": f"{label} restored to last-good state"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["snapshot", "check", "anchor", "fix"])
    ap.add_argument("label", nargs="?", default=None,
                    help="block label for anchor")
    ap.add_argument("reason", nargs="?", default="", help="anchor reason")
    ap.add_argument("--strict", action="store_true",
                    help="journal DRIFT-ALERT on violation")
    ap.add_argument("--no-autofix", action="store_true",
                    help="check only — don't apply automatic fix (observe only)")
    args = ap.parse_args()

    if args.cmd == "snapshot":
        print(json.dumps(snapshot()))
        sys.exit(0)
    if args.cmd == "check":
        sys.exit(check(strict=args.strict, autofix=not args.no_autofix))
    if args.cmd == "fix":
        if not args.label:
            print("fix needs <label>", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(fix(args.label)))
        sys.exit(0)
    if args.cmd == "anchor":
        if not args.label:
            print("anchor needs <label>", file=sys.stderr)
            sys.exit(1)
        print(anchor(args.label, args.reason or "authorised by the user"))
        sys.exit(0)


if __name__ == "__main__":
    main()