"""Split mcp_servers/email_server.py into mcp_servers/email_server/ package."""

raise SystemExit(
    "Unsafe legacy splitter disabled: a line-based cut separated an MCP "
    "decorator from its handler and broke private module scope. Maintain the "
    "existing package files directly."
)

import os

BASE = r'D:\GitHub\odysseus'
SRC = os.path.join(BASE, 'mcp_servers', 'email_server.py')
PKG = os.path.join(BASE, 'mcp_servers', 'email_server')
os.makedirs(PKG, exist_ok=True)

with open(SRC, encoding='utf-8') as f:
    lines = f.readlines()

# Split: helpers (1-1835), list_tools+call_tool+run (1836-end)
helper_lines = lines[:1835]
main_lines = lines[1835:]

# Write _utils.py
utils_path = os.path.join(PKG, '_utils.py')
with open(utils_path, 'w', encoding='utf-8') as f:
    f.writelines(helper_lines)
print(f"[OK] _utils.py: {len(helper_lines)} lines")

# Write __init__.py
init_path = os.path.join(PKG, '__init__.py')
with open(init_path, 'w', encoding='utf-8') as f:
    f.write('"""Email MCP server package -- split from email_server.py"""\n\n')
    f.write('# Re-export all helpers\n')
    f.write('from mcp_servers.email_server._utils import *  # noqa: F403\n\n')
    f.write('# Main MCP entry points\n')
    f.writelines(main_lines)
print(f"[OK] __init__.py: {len(main_lines) + 3} lines (with header)")

# Update builtin_mcp.py path reference
builtin_mcp = os.path.join(BASE, 'src', 'builtin_mcp.py')
with open(builtin_mcp, encoding='utf-8') as f:
    content = f.read()

old = '"mcp_servers/email_server.py"'
new = '"mcp_servers/email_server/__init__.py"'
if old in content:
    content = content.replace(old, new)
    with open(builtin_mcp, 'w', encoding='utf-8') as f:
        f.write(content)
    print("[OK] Updated builtin_mcp.py path")
else:
    print("[WARN] Path not found in builtin_mcp.py")

# Update test files that read the file path
for test_file in [
    'tests/test_email_registry_sync.py',
    'tests/test_icloud_imap_full_fetch.py',
    'tests/test_imap_mailbox_quoting.py',
]:
    path = os.path.join(BASE, test_file)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    if 'mcp_servers/email_server.py' in content:
        content = content.replace('mcp_servers/email_server.py', 'mcp_servers/email_server/__init__.py')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[OK] Updated {test_file}")

print("\nDone! Original preserved at mcp_servers/email_server.py")
