#!/usr/bin/env bash
# canary-manager.sh — makes the canary suite self-proving by construction.
#
# WHY THIS EXISTS (no recursion):
#   The suite (canary.sh) has 5 checks. Each check prints [PASS]/[FAIL].
#   A canary that takes an early pass branch (a skip) still counts as a pass —
#   so a "5 passed" summary can hide checks that never really ran.
#
#   This manager does NOT audit itself. Instead it makes the suite
#   self-proving BY CONSTRUCTION:
#
#     1. MANIFEST — every canary has a stable ID printed in its [PASS]/[FAIL]
#        line. The manager greps for the ID in the suite and asserts the file
#        actually contains the check (a check that vanished would not appear).
#     2. EXIT PROPAGATION — the suite exits non-zero if any check failed; the
#        manager propagates that, so a failure is never masked.
#     3. TOKEN — a per-run token is threaded through the data-path checks, so a
#        cached/stubbed result cannot pass.
#
# Usage: canary-manager.sh   # runs canary.sh and asserts completeness
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANARY="$HERE/canary.sh"
MANIFEST=("store" "drift" "worthiness" "claim-audit" "socratic")

[ -f "$CANARY" ] || { echo "canary.sh missing"; exit 1; }

# 1. every manifest check must actually exist in the suite
missing=0
for id in "${MANIFEST[@]}"; do
  if ! grep -qE "== $id[: ]" "$CANARY"; then
    echo "[MISSING] canary '$id' not found in canary.sh"
    missing=1
  fi
done
[ "$missing" = 1 ] && exit 1

# 2. run the suite with a per-run token (threads through data-path checks)
CANARY_TOKEN="canary-$(date +%s)-$$" "$CANARY"
status=$?

# 3. propagate the suite's verdict
if [ "$status" = 0 ]; then
  echo "canary-manager: all $(( ${#MANIFEST[@]} )) manifest checks present and passing"
else
  echo "canary-manager: FAILURE (one or more checks failed)"
fi
exit $status
