#!/usr/bin/env python3
"""Render (never install) the per-agent Wiki Steward configuration into an output directory.

    wiki-install.py render --wiki-root F:\\Projects\\Odysseus-Wiki --out <dir> [--python <exe>]

Writes, under <dir>:
    hermes/mcp_servers.wiki.yaml        snippet to merge into a Hermes profile's config.yaml
    wiki-bot-profile/config.yaml        the complete Wiki Bot profile (wiki tools only)
    claude-code/.mcp.json               project-scoped MCP file for Claude Code
    codex/config.toml.snippet           [mcp_servers.wiki] block for Codex
    RENDERED.md                         what was written and how to install each file by hand

Nothing outside <dir> is touched. Installing a file is a separate, explicit step.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'integrations' / 'wiki' / 'wiki-bot-profile' / 'config.yaml.template'
PLATFORMS = ['api_server', 'bluebubbles', 'cli', 'cron', 'dingtalk', 'discord', 'email', 'feishu', 'matrix', 'mattermost', 'qqbot', 'signal',
             'slack', 'telegram', 'webhook', 'wecom', 'wecom_callback', 'weixin', 'whatsapp', 'whatsapp_cloud']


def q(path):
    return str(path).replace('\\', '/')


def render(wiki_root, out, python):
    script = ROOT / 'scripts' / 'wiki_mcp.py'
    out.mkdir(parents=True, exist_ok=True)

    def server_yaml(agent):
        return ('wiki:\n  command: "%s"\n  args: ["%s"]\n  env:\n    WIKI_ROOT: "%s"\n    WIKI_AGENT: "%s"\n    PYTHONPATH: ""\n  connect_timeout: 60\n'
                % (q(python), q(script), q(wiki_root), agent))

    (out / 'hermes').mkdir(exist_ok=True)
    (out / 'hermes' / 'mcp_servers.wiki.yaml').write_text('mcp_servers:\n' + ''.join('  ' + l + '\n' for l in server_yaml('hermes').splitlines()), encoding='utf-8', newline='\n')
    text = TEMPLATE.read_text(encoding='utf-8')
    for key, val in (('<python>', q(python)), ('<wiki_mcp_script>', q(script)), ('<wiki_root>', q(wiki_root)),
                     ('<platform_toolsets>', '\n'.join('  %s: [wiki]' % p for p in PLATFORMS))):
        text = text.replace(key, val)
    (out / 'wiki-bot-profile').mkdir(exist_ok=True)
    (out / 'wiki-bot-profile' / 'config.yaml').write_text(text, encoding='utf-8', newline='\n')
    (out / 'claude-code').mkdir(exist_ok=True)
    (out / 'claude-code' / '.mcp.json').write_text(json.dumps({'mcpServers': {'wiki': {'command': q(python), 'args': [q(script)], 'env': {
        'WIKI_ROOT': q(wiki_root), 'WIKI_AGENT': 'claude-code', 'PYTHONPATH': ''}}}}, indent=2) + '\n', encoding='utf-8', newline='\n')
    (out / 'codex').mkdir(exist_ok=True)
    (out / 'codex' / 'config.toml.snippet').write_text(
        '[mcp_servers.wiki]\ncommand = "%s"\nargs = ["%s"]\nstartup_timeout_sec = 60\ntool_timeout_sec = 120\n\n[mcp_servers.wiki.env]\nWIKI_ROOT = "%s"\nWIKI_AGENT = "codex"\nPYTHONPATH = ""\n'
        % (q(python), q(script), q(wiki_root)), encoding='utf-8', newline='\n')
    (out / 'RENDERED.md').write_text(
        '# Rendered Wiki Steward configuration\n\nWiki: `%s`\nMCP server: `%s`\nPython: `%s`\n\n'
        '| File | Install by hand (each is a deliberate, separate step) |\n|---|---|\n'
        '| `hermes/mcp_servers.wiki.yaml` | merge the `mcp_servers:` block into the config.yaml of the Hermes profile that should use the wiki |\n'
        '| `wiki-bot-profile/config.yaml` | copy to `<hermes home>/profiles/wiki-bot/config.yaml`, then pick a model for that profile |\n'
        '| `claude-code/.mcp.json` | copy into the root of the project that Claude Code works in (project scope; ask before committing it) |\n'
        '| `codex/config.toml.snippet` | append to `~/.codex/config.toml` after `codex login`, or pass each key with `codex -c` per run |\n'
        % (wiki_root, script, python), encoding='utf-8', newline='\n')
    return sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('render')
    r.add_argument('--wiki-root', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--python', default=sys.executable)
    a = ap.parse_args()
    print(json.dumps(render(Path(a.wiki_root), Path(a.out), Path(a.python)), indent=1))


if __name__ == '__main__':
    main()
