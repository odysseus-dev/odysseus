"""Does Hermes' deferred-tool bridge (tool_search / tool_describe / tool_call) expose anything beyond the profile's toolsets?

    <hermes venv python> bridge_probe.py <profile_dir>

Calls the bridge's own handlers in-process with the profile config. No model, no gateway.
"""
import json
import os
import sys
from pathlib import Path

profile = Path(sys.argv[1])
os.environ['HERMES_HOME'] = str(profile)
os.environ.pop('HERMES_WRITE_SAFE_ROOT', None)

from ruamel.yaml import YAML  # noqa: E402
from hermes_cli import tools_config  # noqa: E402
from tools import mcp_tool_discovery as discovery  # noqa: E402
from tools.registry import registry  # noqa: E402
import model_tools  # noqa: E402

config = YAML(typ='safe').load((profile / 'config.yaml').read_text(encoding='utf-8'))
discovery.register_mcp_servers(config.get('mcp_servers') or {})
enabled = sorted(tools_config._get_platform_tools(config, 'cli'))
defs = model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)
bridge = {d['function']['name']: d['function'] for d in defs}
out = {'enabled': enabled, 'schemas': {n: json.dumps(f.get('parameters'))[:300] for n, f in bridge.items()}}


from tools import tool_search as ts  # noqa: E402

unassembled = model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True, skip_tool_search_assembly=True) or []
scoped = sorted(ts.scoped_deferrable_names(unassembled))
out['scoped_deferrable_names'] = scoped
probe_names = ['terminal', 'write_file', 'read_file', 'patch', 'execute_code', 'delegate_task', 'memory', 'skill_manage', 'browser_exec', 'cronjob_manage',
               'mcp__wiki__wiki_search', 'mcp__wiki__wiki_resolve', 'mcp__wiki__wiki_decide', 'mcp__wiki__wiki_adopt']
out['in_scope'] = {n: (n in scoped) for n in probe_names}
out['unwrapped_underlying'] = {}
for n in ('terminal', 'write_file', 'mcp__wiki__wiki_search'):
    under, args, err = ts.resolve_underlying_call({'calls': [{'name': n, 'arguments': {}}]}) if hasattr(ts, 'resolve_underlying_call') else (None, None, 'no resolver')
    out['unwrapped_underlying'][n] = {'underlying': under, 'err': (str(err) or '')[:120], 'scope_block': (ts.scope_block_reason(n) or '')[:160] if under and under not in scoped else None}
print(json.dumps(out, default=str))
