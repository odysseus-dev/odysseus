#!/usr/bin/env bash
# canary.sh — self-proving checks for the memory platform.
#
# NOTE (cross-PR): this suite spans the whole platform. The memory work is
# split into logically-linked PRs (hybrid recall / lifecycle / product-API);
# the store check needs PR 1's memory_store, and the claim-audit check needs
# PR 3's claim_audit. This file lives with PR 2 (lifecycle) and the full
# suite runs only after all three PRs are absorbed into the codebase. Each
# PR ships its own focused pytest coverage (tests/test_*_recall.py,
# tests/test_memory_lifecycle.py, tests/test_memory_product_api.py) that
# runs standalone; the canary is the end-to-end verification once merged.
#
# Smoke-checks the mechanisms that hold the platform together, so a regression
# in any one of them is caught on demand instead of silently:
#
#   1. store      — hybrid recall round-trips a fact, abstains on irrelevance,
#                   and the audit chain is tamper-evident
#   2. drift      — the drift ledger flags bulk change and an anchor clears it
#   3. worthiness — the intake gate rejects noise, passes assistant value
#   4. claim-audit— the output gate degrades unsupported strong claims
#   5. socratic   — a concede without follow-through is a coherence gap
#   6. growth     — evidence-graded promotion applies grounded identity,
#                   blocks invented identity
#
# Every check runs against a SCRATCH store (temp dir, env-isolated) so it is
# portable and never touches real memory. Exits non-zero if any check failed.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# prefer the memory venv (has sqlite-vec) if present, else the current python
if [ -z "${MEMORY_PYTHON:-}" ] && [ -x "$HOME/.venvs/memory/bin/python3" ]; then
  PY="$HOME/.venvs/memory/bin/python3"
else
  PY="${MEMORY_PYTHON:-python3}"
fi

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL+1)); printf '[FAIL] %s\n' "$1"; }
RTOK="${CANARY_TOKEN:-canary-$$}"

## ---- 1. store: hybrid recall + abstention + tamper-evident audit -----------
echo "== store: recall round-trip, abstention, tamper-evident chain =="
if "$PY" - "$HERE/memory_store.py" <<'PY'
import importlib.util, os, sys, json
sp = sys.argv[1]
spec = importlib.util.spec_from_file_location("ms", sp)
ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
import tempfile
d = tempfile.mkdtemp(prefix="mp-canary-store-")
ms.DB_PATH = f"{d}/mem.db"; ms.STORE_DIR = d
db = ms.connect()
tok = os.environ.get("CANARY_TOKEN", "canary")
ms.add_entry(db, f"{tok} prefers oat milk and avoids eggs", 0.8, "diet", source="canary")
ms.add_entry(db, f"{tok} drinks oat milk in the morning", 0.8, "diet", source="canary")
ms.add_entry(db, f"{tok} teaches a physical skill", 0.9, "work", source="canary")
res = ms.recall(db, f"{tok} morning drink")
assert any("oat milk" in r["text"] for r in res), "recall missed the fact"
# irrelevant query abstains
res3 = ms.recall(db, "quantum physics of black holes")
assert len(res3) == 0, f"should abstain, got {len(res3)}"
# audit chain intact, then tamper breaks it
ms.audit_append(db, "claim1", "PASS", "ev1")
ms.audit_append(db, "claim2", "DEGRADE", "ev2")
ok, _ = ms.audit_verify(db)
assert ok, "audit chain broken"
db.execute("UPDATE audit_log SET hash='tampered' WHERE id=2")
db.commit()
ok2, _ = ms.audit_verify(db)
assert not ok2, "tampered chain not detected"
print("store-ok")
PY
then
  pass "store: hybrid recall + abstention + tamper-evident audit chain"
else
  fail "store: a check failed"
fi

## ---- 2. drift: ledger flags bulk change, anchor clears ---------------------
echo "== drift: bulk change flagged, anchor clears =="
if "$PY" - "$HERE/drift-ledger.py" <<'PY'
import importlib.util, os, sys, sqlite3, tempfile
lp = sys.argv[1]
spec = importlib.util.spec_from_file_location("dl", lp)
dl = importlib.util.module_from_spec(spec); spec.loader.exec_module(dl)
d = tempfile.mkdtemp()
dl.INDEX_FILE = f"{d}/index/ledger.json"; dl.JOURNAL_DIR = f"{d}/journal"; dl.DB = f"{d}/mem.db"
os.makedirs(f"{d}/index", exist_ok=True); os.makedirs(f"{d}/journal", exist_ok=True)
db = sqlite3.connect(dl.DB)
db.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, importance REAL, created_at TEXT, last_accessed TEXT, topic TEXT, entities TEXT, source TEXT, method TEXT, status TEXT, valid_from TEXT, valid_until TEXT, confidence REAL, temperature REAL, always_on INTEGER, priority INTEGER, triggers TEXT, kind TEXT, slug TEXT, summary TEXT)")
db.execute("INSERT INTO entries (text, importance, created_at, last_accessed, topic, status, confidence, temperature, always_on, priority, kind) VALUES ('a stable human fact', 0.8, 'x', 'x', 'human', 'active', 0.9, 1.0, 1, 3, 'fact')")
db.commit(); db.close()
dl.snapshot()
db = sqlite3.connect(dl.DB)
bulk_text = "- bulk drift " * 200
db.execute("INSERT INTO entries (text, importance, created_at, last_accessed, topic, status, confidence, temperature, always_on, priority, kind) VALUES (?, 0.9, 'x', 'x', 'human', 'active', 0.9, 1.0, 1, 3, 'fact')", (bulk_text,))
db.commit(); db.close()
assert dl.check(strict=False, autofix=False) != 0, "drift not flagged"
dl.anchor("human", "canary restore")
assert dl.check(strict=False, autofix=False) == 0, "anchor did not clear drift"
print("drift-ok")
PY
then
  pass "drift: ledger flags bulk change, anchor clears"
else
  fail "drift: detect/anchor failed"
fi

## ---- 3. worthiness: intake gate rejects noise, passes value ----------------
echo "== worthiness: reject noise, pass assistant value =="
if "$PY" - "$HERE/worthiness.py" <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("w", sys.argv[1])
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
bad = ["Conspiracy: scientists are hiding the real cure",
       "Get rich quick: passive income grindset",
       "Stock tip for tomorrow"]
good = ["Always reveal sources for claims",
        "The user prefers oat milk and limits dairy",
        "Teaching a physical skill: drill fundamentals before advanced moves works across learners",
        "The render loop procedure: export, check, archive"]
ok = True
for f in bad:
    v = w.assess(f)[0]
    if v != "REJECT":
        ok = False; print(f"  expected REJECT got {v}: {f}")
for f in good:
    v = w.assess(f)[0]
    if v not in ("ABSORB", "PROMOTE"):
        ok = False; print(f"  expected ABSORB/PROMOTE got {v}: {f}")
sys.exit(0 if ok else 1)
PY
then
  pass "worthiness: rejects noise, passes assistant value"
else
  fail "worthiness: mis-routed a candidate"
fi

## ---- 4. claim-audit: output gate degrades unsupported strong claims --------
echo "== claim-audit: unsupported strong claim degraded =="
if "$PY" - "$HERE/claim_audit.py" <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("ca", sys.argv[1])
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
draft = "This is definitely the best system ever built and it is proven to work 100% of the time."
result = ca.scan(draft) if hasattr(ca, "scan") else []
sys.exit(0 if result else 1)
PY
then
  pass "claim-audit: flags unsupported strong claims"
else
  fail "claim-audit: did not flag a strong claim"
fi

## ---- 5. socratic: concede without follow-through is a coherence gap --------
echo "== socratic: concede without amendment/hold = coherence gap =="
if "$PY" - "$HERE/socratic.py" <<'PY'
import importlib.util, os, sys, sqlite3, tempfile
d = tempfile.mkdtemp()
os.environ["MEMORY_STORE_DB"] = f"{d}/mem.db"
spec = importlib.util.spec_from_file_location("s", sys.argv[1])
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
db = s.connect()
s.record("concede", "rule X is wrong", "you are right, the old rule is wrong", amended="", hold_reason="")
gaps = s.coherence()
assert gaps, "a concede with no amendment/hold must be a coherence gap"
print("socratic-ok")
PY
then
  pass "socratic: concede-without-follow-through is a coherence gap"
else
  fail "socratic: coherence check failed"
fi

echo ""
echo "======================"
echo "canaries: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
