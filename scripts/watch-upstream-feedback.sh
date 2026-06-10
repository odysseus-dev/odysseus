#!/usr/bin/env bash
# Poll GitHub for new activity on your upstream Odysseus PRs and related issues.
# Usage:
#   ./scripts/watch-upstream-feedback.sh          # one-shot report
#   ./scripts/watch-upstream-feedback.sh --watch  # repeat every 5 minutes
#   ./scripts/watch-upstream-feedback.sh --watch 120  # custom interval (seconds)
set -euo pipefail

REPO="${UPSTREAM_REPO:-pewdiepie-archdaemon/odysseus}"
AUTHOR="${GITHUB_AUTHOR:-giuliozelante}"
STATE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/odysseus-upstream-watch.json"
WATCH=false
INTERVAL=300

while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch) WATCH=true; shift ;;
    -h|--help)
      sed -n '2,6p' "$0"
      exit 0
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then INTERVAL="$1"; else echo "Unknown arg: $1" >&2; exit 2; fi
      shift
      ;;
  esac
done

mkdir -p "$(dirname "$STATE_FILE")"
[[ -f "$STATE_FILE" ]] || echo '{}' >"$STATE_FILE"

report() {
  local now prs issues notifications
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "=== Odysseus upstream watch ($now) ==="
  echo "Repo: $REPO | Author: $AUTHOR"
  echo

  prs="$(gh pr list --repo "$REPO" --author "$AUTHOR" --state all --limit 20 \
    --json number,title,state,updatedAt,url,reviewDecision 2>/dev/null || echo '[]')"
  echo "## Your PRs"
  echo "$prs" | jq -r '.[] | "- #\(.number) [\(.state)] \(.title) (review: \(.reviewDecision // "none"))\n  \(.url) updated \(.updatedAt)"'
  echo

  issues="$(gh search issues --repo "$REPO" --author "$AUTHOR" --limit 10 \
=======
  issues="$(gh issue list --repo "$REPO" --author "$AUTHOR" --state all --limit 10 \
    --json number,title,state,updatedAt,url 2>/dev/null || echo '[]')"
  echo "## Your issues"
  if [[ "$(echo "$issues" | jq 'length')" -eq 0 ]]; then
    echo "(none)"
  else
    echo "$issues" | jq -r '.[] | "- #\(.number) [\(.state)] \(.title)\n  \(.url) updated \(.updatedAt)"'
  fi
  echo

  echo "## Recent notifications (odysseus)"
  gh api user/notifications --paginate -q '.[] | select(.repository.full_name == "'"$REPO"'") | "- [\(.reason)] \(.subject.title)\n  \(.subject.url) @ \(.updated_at)"' 2>/dev/null | head -20 || echo "(none or gh not authenticated)"
  echo

  # Highlight PRs with fresh review/comment activity since last run.
  local prev cur changes
  prev="$(cat "$STATE_FILE")"
  cur="$(echo "$prs" | jq -c '[.[] | {number, updatedAt, reviewDecision}]')"
  changes="$(jq -n --argjson prev "$prev" --argjson cur "$cur" '
    [$cur[] as $n |
      ($prev[]? | select(.number == $n.number)) as $p |
      select($p == null or $p.updatedAt != $n.updatedAt or ($p.reviewDecision // "") != ($n.reviewDecision // "")) |
      $n]')"
  if [[ "$(echo "$changes" | jq 'length')" -gt 0 ]]; then
    echo "## Changed since last check"
    echo "$changes" | jq -r '.[] | "- PR #\(.number) updated \(.updatedAt) review=\(.reviewDecision // "none")"'
    echo
  fi
  echo "$cur" | jq -c '{prs: ., checked_at: "'"$now"'"}' >"$STATE_FILE"
}

while true; do
  report
  if ! $WATCH; then break; fi
  echo "Sleeping ${INTERVAL}s… (Ctrl+C to stop)"
  sleep "$INTERVAL"
done
