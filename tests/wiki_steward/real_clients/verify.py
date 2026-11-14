"""Verify what the real clients actually did, from the wiki itself and from the clients' tool-call logs.

    python verify.py <env.json>
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
import wiki_steward as ws  # noqa: E402


def tool_calls(path):
    """Real tool invocations only: Claude `tool_use` blocks and Codex `mcp_tool_call` items, with their outcome."""
    calls = []
    if not path.exists():
        return calls
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for block in ((ev.get('message') or {}).get('content') or []):
            if isinstance(block, dict) and block.get('type') == 'tool_use' and str(block.get('name', '')).startswith('mcp__wiki__'):
                calls.append(block['name'].split('__')[-1])
        item = ev.get('item') or {}
        if item.get('type') == 'mcp_tool_call' and ev.get('type') == 'item.completed':
            calls.append('%s%s' % (item.get('tool'), '' if item.get('status') == 'completed' else ' [%s]' % item.get('status')))
    return calls


def main():
    env = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    wiki, scratch = ws.Wiki(env['wiki']), Path(env['scratch'])
    out = {'integrity': wiki.integrity()}
    claims = wiki._claims('odysseus')
    out['claims_by_agent'] = {}
    for c in claims:
        out['claims_by_agent'].setdefault(c['agents'][0], []).append({'subject': c.get('subject'), 'status': c['status'], 'grade': c['grade']})
    out['tasks'] = [{'agent': t['agent'], 'status': t['status'], 'summary': t['summary'][:40]} for t in wiki._tasks('odysseus') if not t['id'].startswith('git')]
    audit = [json.loads(l) for l in (wiki.root / 'audit' / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    out['audit_actors'] = sorted({a['actor'] for a in audit})
    out['audit_actions'] = {}
    for a in audit:
        out['audit_actions'][a['actor']] = out['audit_actions'].get(a['actor'], 0) + 1
    out['conflicts'] = [{'id': c['id'], 'subject': c.get('subject'), 'with': c['conflicts_with']} for c in claims if c['status'] == 'conflict']
    out['other_project_claims'] = len(wiki._claims('other'))
    out['claude_calls'] = tool_calls(scratch / 'out-claude.jsonl')
    out['codex_calls'] = tool_calls(scratch / 'out-codex.jsonl')
    out['commits_not_by_steward'] = [p for p in out['integrity'] if 'not made by the steward' in p]
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
