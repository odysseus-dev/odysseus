#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--check]\n' "$0"
}

mode=apply
case "${1:-}" in
  '') ;;
  --check) mode=check ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  repo_root="$script_root"
fi
if [[ ! -f "$repo_root/static/index.html" ]]; then
  echo 'ERROR: run this script from inside the Hellaine repository.' >&2
  exit 1
fi
cd "$repo_root"

required_files=(
  static/index.html
  static/login.html
  static/manifest.json
  static/style.css
  static/sw.js
  static/app.js
  static/js/models.js
  static/js/sessions.js
  static/js/chatRenderer.js
  static/js/slashCommands.js
  static/js/theme.js
  static/hellaine-logo.svg
  static/icons/favicon.ico
  static/icons/favicon-16x16.png
  static/icons/favicon-32x32.png
  static/icons/favicon-48x48.png
  static/icons/apple-touch-icon.png
  static/icons/android-chrome-192x192.png
  static/icons/android-chrome-512x512.png
  static/icons/icon-maskable-512.png
)

# README.md and docs/ are intentionally excluded from the production Docker
# image. Validate them in a source checkout, but permit a packaged runtime in
# which both source-only artifacts are absent. A partial source checkout is an
# error because it cannot reliably preserve the documented branding.
source_files=(README.md docs/hellaine-logo.svg)
source_files_present=0
for path in "${source_files[@]}"; do
  [[ -e "$path" ]] && ((source_files_present += 1))
done
if (( source_files_present > 0 && source_files_present < ${#source_files[@]} )); then
  echo 'ERROR: source branding artifacts are incomplete; expected README.md and docs/hellaine-logo.svg together.' >&2
  exit 1
fi
if (( source_files_present == ${#source_files[@]} )); then
  required_files+=("${source_files[@]}")
fi

missing=0
for path in "${required_files[@]}"; do
  if [[ ! -s "$path" ]]; then
    echo "ERROR: required branding file missing or empty: $path" >&2
    missing=1
  fi
done
(( missing == 0 )) || exit 1

python3 - "$mode" <<'PY'
import json
import sys
from pathlib import Path

mode = sys.argv[1]
changed = []
errors = []

replacements = {
    "static/index.html": [
        ("<title>Odysseus Chat</title>", "<title>Hellaine's Jade Palace</title>"),
        ("Odysseus Logo", "Hellaine Insignia"),
        ("<span class=\"sidebar-brand-title\">Odysseus</span>", "<span class=\"sidebar-brand-title\">Hellaine</span>"),
        ("<h1 class=\"a11y-visually-hidden\">Odysseus</h1>", "<h1 class=\"a11y-visually-hidden\">Hellaine</h1>"),
        (">Odysseus Chat</span>", ">Hellaine's Jade Palace</span>"),
        ("placeholder=\"Message Odysseus...\"", "placeholder=\"Consult Hellaine...\""),
        ("Used to build clickable links back to Odysseus", "Used to build clickable links back to Hellaine"),
        ("output from the Odysseus process", "output from the Hellaine process"),
    ],
    "static/login.html": [
        ("<title>Odysseus — Login</title>", "<title>Hellaine's Jade Palace — Login</title>"),
        (">Authorize</button>", ">Prove Your Authorization</button>"),
        ("submitBtn.textContent = 'Authorize';", "submitBtn.textContent = 'Prove Your Authorization';"),
    ],
    "static/app.js": [
        ("'Message Odysseus...'", "'Consult Hellaine...'"),
        ('"Odysseus Chat"', '"Hellaine\'s Jade Palace"'),
    ],
    "static/js/models.js": [
        ("Welcome to Odysseus", "Intelligence without compromise"),
    ],
    "static/js/sessions.js": [
        ("'Odysseus Chat'", '"Hellaine\'s Jade Palace"'),
    ],
    "static/js/chatRenderer.js": [
        ("compactRole.textContent = 'Odysseus';", "compactRole.textContent = 'Hellaine';"),
        ("? 'Odysseus' : modelRouteLabel", "? 'Hellaine' : modelRouteLabel"),
    ],
    "static/js/slashCommands.js": [
        ("role.textContent = 'Odysseus';", "role.textContent = 'Hellaine';"),
        ("spinnerRole.textContent = 'Odysseus';", "spinnerRole.textContent = 'Hellaine';"),
        ("Welcome to Odysseus! Lets begin the tour!", "Welcome to Hellaine. Let’s begin the tour."),
        ("Odysseus is yours to explore, enjoy the voyage!", "Hellaine is yours to explore. Enjoy the voyage."),
        ("Odysseus comes with private built-in", "Hellaine comes with private built-in"),
        ("Odysseus is yours to customize", "Hellaine is yours to customize"),
        ("The one Odysseus reaches for", "The one Hellaine reaches for"),
        ("how Odysseus nudges you", "how Hellaine nudges you"),
        ("ask Odysseus in chat", "ask Hellaine in chat"),
        ("want Odysseus to handle", "want Hellaine to handle"),
        ("Odysseus will create the task", "Hellaine will create the task"),
    ],
    "static/js/theme.js": [
        ("label: 'Odysseus Logo'", "label: 'Hellaine Insignia'"),
    ],
    "static/js/keyboard-shortcuts.js": [
        ("'Odysseus Chat'", "'Hellaine Chat'"),
    ],
}

for filename, pairs in replacements.items():
    path = Path(filename)
    if not path.exists():
        continue
    original = path.read_text(encoding="utf-8")
    updated = original
    for upstream, branded in pairs:
        if upstream in updated:
            if mode == "apply":
                updated = updated.replace(upstream, branded)
            else:
                errors.append(f"{filename}: visible upstream text remains: {upstream}")
    if mode == "apply" and updated != original:
        path.write_text(updated, encoding="utf-8")
        changed.append(filename)

manifest_path = Path("static/manifest.json")
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    errors.append(f"static/manifest.json: invalid JSON: {exc}")
else:
    desired_icons = [
        {"src": "/static/icons/favicon-16x16.png?v=hellaine-3", "sizes": "16x16", "type": "image/png"},
        {"src": "/static/icons/favicon-32x32.png?v=hellaine-3", "sizes": "32x32", "type": "image/png"},
        {"src": "/static/icons/favicon-48x48.png?v=hellaine-3", "sizes": "48x48", "type": "image/png"},
        {"src": "/static/icons/android-chrome-192x192.png?v=hellaine-3", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icons/android-chrome-512x512.png?v=hellaine-3", "sizes": "512x512", "type": "image/png"},
        {"src": "/static/icons/icon-maskable-512.png?v=hellaine-3", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    desired = {
        "name": "Hellaine's Jade Palace",
        "short_name": "Hellaine",
        "background_color": "#06120d",
        "theme_color": "#0b1d14",
        "icons": desired_icons,
    }
    mismatches = [key for key, value in desired.items() if manifest.get(key) != value]
    if mismatches and mode == "check":
        errors.append("static/manifest.json: incorrect Hellaine fields: " + ", ".join(mismatches))
    elif mismatches:
        manifest.update(desired)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        changed.append(str(manifest_path))

required_markers = {
    "README.md": ["docs/hellaine-logo.svg", "Hellaine's Jade Palace", "Intelligence without compromise", "original Odysseus project", "LICENSE", "ACKNOWLEDGMENTS.md"],
    "static/index.html": ["Hellaine's Jade Palace", "/static/hellaine-logo.svg", "Consult Hellaine...", "/static/manifest.json", "/static/icons/favicon.ico"],
    "static/login.html": ["Hellaine's Jade Palace — Login", "/static/hellaine-logo.svg", "Prove Your Authorization", "/static/icons/favicon.ico"],
    "static/style.css": ["Hellaine Jade Palace branding overrides", "--bg: #06120d", "--panel: #0b1d14", "--red: #d4af37", "--brand-color: #d4af37"],
    "static/sw.js": ["hellaine-cache-"],
    "static/app.js": ["Consult Hellaine..."],
    "static/js/models.js": ["Intelligence without compromise"],
    "static/js/sessions.js": ["Hellaine's Jade Palace"],
    "static/js/chatRenderer.js": ["Hellaine"],
    "static/js/slashCommands.js": ["Welcome to Hellaine", "Hellaine is yours to explore"],
    "static/js/theme.js": ["Hellaine Insignia", "/static/icons/favicon.ico", "Jade Palace"],
}

for filename, markers in required_markers.items():
    if not Path(filename).exists():
        # Source-only documentation is absent from the production Docker image.
        if filename == "README.md":
            continue
        errors.append(f"{filename}: protected branding file missing")
        continue
    text = Path(filename).read_text(encoding="utf-8")
    for marker in markers:
        if marker not in text:
            errors.append(f"{filename}: protected marker missing: {marker}")

if changed:
    print("Updated visible Hellaine branding in: " + ", ".join(sorted(set(changed))))
elif mode == "apply":
    print("Visible Hellaine branding already applied; no files changed.")

if errors:
    for error in errors:
        print("ERROR: " + error, file=sys.stderr)
    print("Compare the named file with HELLAINE_CUSTOMIZATIONS.md and the dated backup refs.", file=sys.stderr)
    raise SystemExit(1)

print("Hellaine branding check passed.")
PY
