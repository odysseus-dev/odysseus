#!/usr/bin/env bash
set -euo pipefail

# Run this from the root of the Hellaine repo.
# It patches files that were not included in the uploaded zip but may still
# contain visible Odysseus labels.

if [ -f static/js/chat.js ]; then
  python3 - <<'INNER_PY'
from pathlib import Path
p=Path('static/js/chat.js')
s=p.read_text()
s=s.replace('<div class="role">Odysseus</div>', '<div class="role">Hellaine</div>')
s=s.replace("<div class='role'>Odysseus</div>", "<div class='role'>Hellaine</div>")
s=s.replace("role.textContent = 'Odysseus';", "role.textContent = 'Hellaine';")
s=s.replace('role.textContent = "Odysseus";', 'role.textContent = "Hellaine";')
p.write_text(s)
INNER_PY
fi

# Keep notification icons on the new /static/icons path.
for f in static/js/calendar/reminders.js static/js/notes.js static/js/settings.js static/js/tasks.js; do
  [ -f "$f" ] || continue
  sed -i     -e "s#/static/favicon\.ico#/static/icons/favicon.ico#g"     -e "s#/static/favicon\.png#/static/icons/favicon-32x32.png#g"     "$f"
done

echo "Hellaine post-fix patch applied. Now git add/commit/push, then pull/build/restart on Lucrezia."
