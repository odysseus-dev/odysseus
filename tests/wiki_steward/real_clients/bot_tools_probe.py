"""Resolve, with the pinned Hermes' own code, exactly which tools a profile config exposes on every platform.

    <hermes venv python> bot_tools_probe.py <profile_dir>     (PYTHONPATH = pinned agent source; HERMES_HOME = the profile dir)

No model is called and no gateway is started.
"""
import json
import os
import sys
from pathlib import Path

profile = Path(sys.argv[1])
os.environ['HERMES_HOME'] = str(profile)
os.environ.pop('HERMES_WRITE_SAFE_ROOT', None)

from ruamel.yaml import YAML  # noqa: E402  (Hermes' own YAML library)
from hermes_cli import tools_config  # noqa: E402
from tools import mcp_tool_discovery as discovery  # noqa: E402
import model_tools  # noqa: E402

config = YAML(typ='safe').load((profile / 'config.yaml').read_text(encoding='utf-8'))
discovery.register_mcp_servers(config.get('mcp_servers') or {})
out = {}
for platform in sorted(tools_config.PLATFORMS):
    enabled = sorted(tools_config._get_platform_tools(config, platform))
    defs = model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)
    names = sorted(d['function']['name'] for d in defs)
    out[platform] = {'toolsets': enabled, 'tools': names}
print(json.dumps(out))
