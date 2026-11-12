"""Scratch environment for the real-client MCP tests. Nothing here touches production, global agent config, or the real wiki.

    python common.py setup <scratch_dir>      creates <scratch_dir>/{source,wiki,env.json}
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
import wiki_steward as ws  # noqa: E402

COOKBOOK = 'def delete_cookbook(path):\n    return remove_tree(path)  # handles read-only files on Windows\n'
AGENTS = ('hermes-preview', 'claude-code', 'codex')


def git(repo, *args):
    return ws.git(repo, '-c', 'user.name=dev', '-c', 'user.email=dev@example.org', *args).stdout.strip()


def setup(scratch):
    scratch = Path(scratch)
    source, wiki_dir = scratch / 'source', scratch / 'wiki'
    source.mkdir(parents=True)
    git(source, 'init', '-q', '-b', 'main')
    (source / 'src').mkdir()
    (source / 'src' / 'cookbook.py').write_text(COOKBOOK, encoding='utf-8', newline='\n')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'Initial cookbook')
    agents = {name: {'projects': ['odysseus'], 'can': list(ws.CAPS)} for name in AGENTS + ('wiki-bot',)}
    agents['guest'] = {'projects': ['other'], 'can': ['search']}
    wiki = ws.Wiki.init(wiki_dir, projects={'odysseus': {'repo': str(source)}, 'other': {'repo': str(source)}}, agents=agents, reviewers=['user'])
    wiki.adopt('user', 'configure')
    env = {'scratch': str(scratch), 'source': str(source), 'wiki': str(wiki_dir), 'python': sys.executable,
           'mcp_script': str(ROOT / 'scripts' / 'wiki_mcp.py'), 'agents': list(AGENTS)}
    (scratch / 'env.json').write_text(json.dumps(env, indent=1), encoding='utf-8')
    return env


if __name__ == '__main__':
    if sys.argv[1:2] == ['setup']:
        print(json.dumps(setup(sys.argv[2]), indent=1))
