#!/usr/bin/env python3
"""End-of-turn hook for Claude Code (Stop) and Codex (notify): record new commits as task logs. PREPARED, NOT INSTALLED.

    wiki_hook.py --root <wiki> --agent <claude-code|codex> [event-json]

Claude Code passes the hook event as JSON on stdin; Codex passes it as the last argument. The content is ignored: the hook only
calls Wiki.sync for every project the agent may record tasks for. sync is idempotent and returns before any write when there is
nothing new, so firing it every turn costs one `git rev-parse` per project.

Fail-open by design: whatever goes wrong, the exit status is 0 and nothing is printed, so an agent is never blocked by the wiki.
Failures are appended to <wiki>/.steward/hook.log (git-ignored, so a failing hook cannot make the wiki look tampered with).
Wiki writes themselves stay fail-closed: a dirty or unreadable wiki simply refuses and the hook logs it.
"""
import argparse
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'src'))


def log(root, line):
    try:
        path = Path(root) / '.steward'
        path.mkdir(exist_ok=True)
        with open(path / 'hook.log', 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%Y-%m-%dT%H:%M:%S'), line))
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--agent', required=True)
    ap.add_argument('event', nargs='?')
    a = ap.parse_args()
    try:
        import wiki_steward as ws
        wiki = ws.Wiki(a.root)
        spec = wiki.config.get('agents', {}).get(a.agent) or {}
        if 'record_task' not in spec.get('can', []):
            return 0
        for project in spec.get('projects', []):
            if project == '*' or project not in wiki.config.get('projects', {}):
                continue
            try:
                res = wiki.sync(a.agent, project)
                if res.get('recorded'):
                    log(a.root, 'hook %s synced %s: %d commits' % (a.agent, project, res['recorded']))
            except ws.WikiError as exc:
                log(a.root, 'hook %s refused on %s: %s' % (a.agent, project, str(exc)[:160]))
    except BaseException as exc:  # noqa: BLE001 - never block the agent
        log(a.root, 'hook %s failed: %s\n%s' % (a.agent, exc, traceback.format_exc()[-600:]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
