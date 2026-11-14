"""Hermes' own MCP client driving the Wiki Steward through a scripted list of tool calls.

    <hermes venv python> hermes_session.py <config.json> <steps.json>

config.json: {"python","mcp_script","wiki","agent"}. steps.json: [{"tool":"wiki_x","args":{...}}, ...]. Output: one JSON object with a result per step.
The server is registered through Hermes' discovery code with a throwaway HERMES_HOME, so nothing global is touched.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

cfg = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
steps = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
os.environ['HERMES_HOME'] = tempfile.mkdtemp(prefix='hermes-session-home-')
os.environ.pop('HERMES_WRITE_SAFE_ROOT', None)

from tools import mcp_tool_discovery as discovery  # noqa: E402
from tools.registry import registry  # noqa: E402

server = {'command': cfg['python'], 'args': [cfg['mcp_script']], 'env': {'WIKI_ROOT': cfg['wiki'], 'WIKI_AGENT': cfg['agent'], 'PYTHONPATH': ''}, 'connect_timeout': 90}
discovery.register_mcp_servers({'wiki': server})
names = {n.split('__')[-1]: n for n in registry.get_all_tool_names() if n.startswith('mcp__wiki__')}
out = {'agent': cfg['agent'], 'tools_visible': sorted(names), 'results': []}
for step in steps:
    tool = step['tool']
    if tool not in names:
        out['results'].append({'tool': tool, 'visible': False})
        continue
    raw = registry.dispatch(names[tool], step.get('args', {}))
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = {'raw': str(raw)[:400]}
    inner = data
    if isinstance(data, dict) and isinstance(data.get('result'), str):
        try:
            inner = json.loads(data['result'])
        except ValueError:
            pass
    out['results'].append({'tool': tool, 'visible': True, 'error': isinstance(data, dict) and 'error' in data, 'data': inner})
print(json.dumps(out, default=str))
