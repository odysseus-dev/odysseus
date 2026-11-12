"""Drive Hermes' own MCP client (tools/mcp_tool, the pinned release) against the Wiki Steward, with an in-memory server
configuration and a throwaway HERMES_HOME. The `hermes` CLI and the live install are not used.

    <hermes venv python> hermes_probe.py <env.json>      (PYTHONPATH must contain the pinned agent source)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

env = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
home = Path(tempfile.mkdtemp(prefix='hermes-probe-home-'))
os.environ['HERMES_HOME'] = str(home)
os.environ.pop('HERMES_WRITE_SAFE_ROOT', None)

from tools import mcp_tool_discovery as discovery  # noqa: E402
from tools.registry import registry  # noqa: E402

server = {'command': env['python'], 'args': [env['mcp_script']], 'env': {'WIKI_ROOT': env['wiki'], 'WIKI_AGENT': 'hermes-preview', 'PYTHONPATH': ''},
          'connect_timeout': 60}
registered = discovery.register_mcp_servers({'wiki': server})
names = sorted(n for n in registry.get_all_tool_names() if 'wiki' in n) if hasattr(registry, 'get_all_tool_names') else sorted(registered)
out = {'home': str(home), 'registered': names}


def call(tool, args):
    name = next((n for n in names if n.endswith(tool)), None)
    if not name:
        return {'missing_tool': tool}
    raw = registry.dispatch(name, args)
    try:
        data = json.loads(raw)
        return data
    except (TypeError, ValueError):
        return {'raw': str(raw)[:600]}


def inner(res):
    if isinstance(res, dict) and 'result' in res and isinstance(res['result'], str):
        try:
            return json.loads(res['result'])
        except ValueError:
            return res
    return res


steps = {}
steps['status'] = call('wiki_status', {'project': 'odysseus'})
steps['begin'] = call('wiki_begin', {'project': 'odysseus', 'summary': 'hermes probe task'})
steps['propose_source'] = call('wiki_propose', {'claim': 'src/cookbook.py handles read-only files on Windows (hermes).', 'project': 'odysseus', 'scope': 'source',
                                                'evidence': [{'type': 'file', 'path': 'src\\cookbook.py', 'contains': 'handles read-only files'}], 'subject': 'probe.hermes.source'})
steps['propose_conflict'] = call('wiki_propose', {'claim': 'Hermes claims the cookbook delete always raises on Windows.', 'project': 'odysseus',
                                                  'evidence': [{'type': 'note', 'text': 'hermes says'}], 'subject': 'probe.conflict'})
steps['denied_project'] = call('wiki_propose', {'claim': 'Hermes tries a project it has no access to.', 'project': 'other', 'evidence': []})
steps['no_decide_tool'] = call('wiki_decide', {'claim_id': 'c-x', 'decision': 'accept'})
steps['identity_override'] = call('wiki_search', {'query': 'cookbook', 'agent': 'sonnet'})
steps['search'] = call('wiki_search', {'query': 'cookbook read-only windows'})
steps['sync'] = call('wiki_sync', {'project': 'odysseus'})
out['steps'] = steps
print(json.dumps(out, default=str))
discovery.shutdown_mcp_servers() if hasattr(discovery, 'shutdown_mcp_servers') else None
