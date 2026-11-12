#!/usr/bin/env python3
"""Wiki Steward command line.

Agents:   wiki.py --root R --agent NAME call METHOD [--args JSON]       (JSON on stdout; JSON args or stdin)
Humans:   wiki.py --root R init | integrity | adopt NOTE | decide ID accept|reject [NOTE] | revert COMMIT
Human-only commands refuse to run without a terminal, so an agent's non-interactive shell cannot use them by accident.
This is a speed bump, not a security boundary: the same user could still run them through a pseudo-terminal.
"""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import wiki_steward as ws  # noqa: E402

METHODS = {'search', 'propose', 'verify', 'begin_task', 'record_task', 'sync', 'handoff', 'status', 'conflicts', 'reconcile', 'inbox', 'ack', 'recheck'}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default=os.environ.get('WIKI_ROOT'))
    ap.add_argument('--agent', default=os.environ.get('WIKI_AGENT'))
    ap.add_argument('--reviewer', default='user')
    sub = ap.add_subparsers(dest='cmd', required=True)
    call = sub.add_parser('call')
    call.add_argument('method')
    call.add_argument('--args', default=None)
    sub.add_parser('init')
    sub.add_parser('integrity')
    v = sub.add_parser('validate')
    v.add_argument('--file', default=None, help='lint this config file instead of the wiki\'s own steward.json')
    for name in ('adopt', 'decide', 'revert'):
        p = sub.add_parser(name)
        p.add_argument('items', nargs='*')
    a = ap.parse_args(argv)
    if not a.root:
        ap.error('--root or WIKI_ROOT is required')
    try:
        if a.cmd == 'init':
            ws.Wiki.init(a.root)
            out = {'initialised': a.root, 'next': 'edit steward.json (projects, agents, reviewers, tests, external), then: wiki.py adopt "configure"'}
        elif a.cmd == 'integrity':
            problems = ws.Wiki(a.root).integrity()
            out = {'ok': not problems, 'problems': problems}
            if problems:
                print(json.dumps(out, indent=1))
                return 2
        elif a.cmd == 'validate':
            wiki = ws.Wiki(a.root)
            errors, notes = wiki.validate_config(json.loads(Path(a.file).read_text(encoding='utf-8')) if a.file else None)
            out = {'ok': not errors, 'errors': errors, 'notes': notes}
            if errors:
                print(json.dumps(out, indent=1))
                return 2
        elif a.cmd == 'call':
            if a.method not in METHODS or not a.agent:
                raise ws.Denied('unknown method or no --agent')
            raw = a.args if a.args is not None else (sys.stdin.read() if not sys.stdin.isatty() else '{}')
            out = getattr(ws.Wiki(a.root), a.method)(a.agent, **json.loads(raw or '{}'))
        else:
            if not sys.stdin.isatty():
                raise ws.Denied('%s needs a terminal; run it yourself' % a.cmd)
            wiki = ws.Wiki(a.root)
            if a.cmd == 'adopt':
                wiki.adopt(a.reviewer, ' '.join(a.items) or 'manual change')
                out = {'adopted': True}
            elif a.cmd == 'decide':
                out = wiki.decide(a.reviewer, a.items[0], a.items[1], ' '.join(a.items[2:]))
            else:
                wiki.revert(a.reviewer, a.items[0])
                out = {'reverted': a.items[0]}
    except ws.WikiError as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}))
        return 1
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
