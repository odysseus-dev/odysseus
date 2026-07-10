"""Quarantined legacy line-based app.js splitter.

Odysseus' app.js is an ES module with shared lexical state. Splitting it at
line numbers produces invalid modules and can partially rewrite index.html.
Use explicit imports/exports and real module boundaries before re-enabling a
split workflow.
"""

raise SystemExit(
    "Unsafe app.js splitter disabled: line-based chunks cannot preserve ES-module scope."
)

import os

BASE = r'D:\GitHub\odysseus'
SRC = os.path.join(BASE, 'static', 'app.js')
JS_DIR = os.path.join(BASE, 'static', 'js')

os.makedirs(JS_DIR, exist_ok=True)

with open(SRC, 'r', encoding='utf-8') as f:
    lines = f.readlines()

def section(start, end):
    return ''.join(lines[start-1:end])

# Split points:
#   _utils.js:  lines 1-531   (utilities, Android helpers, chat model helpers)
#   _events.js: lines 532-3902 (initializeEventListeners)
#   _app.js:    lines 3903-4762 (startOdysseusApp + _initSidebarClock)

chunks = [
    ("_utils.js",   1,    531),
    ("_events.js",  532,  3902),
    ("_app.js",     3903, len(lines)),
]

for fname, start, end in chunks:
    content = section(start, end)
    out = os.path.join(JS_DIR, fname)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(f'// {fname} — chunked from app.js\n')
        f.write(content)
    size_kb = os.path.getsize(out) / 1024
    n = len(content.splitlines())
    print(f'  [OK] {fname}: lines {start}-{end} ({n} lines, {size_kb:.1f} KB)')

# Replace master app.js with a comment + nothing (index.html will load individual files)
# Actually, let's keep app.js as is for backward compat and update index.html instead.
# The old app.js is preserved; index.html should load js/*.js files instead.

# Update index.html
idx_path = os.path.join(BASE, 'static', 'index.html')
with open(idx_path, 'r', encoding='utf-8') as f:
    html = f.read()

old_script = '<script src="/static/app.js"></script>'
new_scripts = '''<!-- JS split from app.js (load order matters) -->
  <script src="/static/js/_utils.js"></script>
  <script src="/static/js/_events.js"></script>
  <script src="/static/js/_app.js"></script>'''

if old_script in html:
    html = html.replace(old_script, new_scripts)
    with open(idx_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  [OK] Updated index.html to load js/*.js files')
else:
    print(f'  [WARN] Could not find "{old_script}" in index.html')
    # Try to find the actual script tag
    import re
    matches = re.findall(r'<script.*?app\.js.*?</script>', html)
    print(f'  Found script tags with app.js: {matches[:3]}')

print(f'\nDone! Created static/js/ with {len(chunks)} files.')
print(f'Original static/app.js preserved for backward compat.')
