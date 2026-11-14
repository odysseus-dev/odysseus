#!/usr/bin/env python3
"""Import the reviewed initial claims for a project through the Wiki Steward's normal propose path.

    wiki-import-claims.py --wiki-root R --claims config/wiki/odysseus-initial-claims.json            (default: check only, writes nothing)
    wiki-import-claims.py --wiki-root R --claims ... --apply                                          (propose each claim as the configured agent)

Nothing is verified here: every claim goes through Wiki.propose, which checks the evidence against the project's repository at HEAD and decides
the status itself (decision, security, policy and architecture claims land in needs-review for the reviewer). A claim that already exists is
reported as a duplicate and not added again. Stdlib only.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import wiki_steward as ws  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--wiki-root', required=True)
    ap.add_argument('--claims', required=True)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    doc = json.load(open(a.claims, encoding='utf-8-sig'))
    wiki = ws.Wiki(a.wiki_root)
    project, agent = doc['project'], doc.get('agent', 'hermes')
    problems = wiki.integrity()
    if problems:
        print('wiki integrity problems, nothing imported:', problems[:3])
        return 2
    repo = wiki._repo(project)
    bad = 0
    for c in doc['claims']:
        for ev in c['evidence']:
            if ev.get('type') == 'file':
                shown = subprocess.run(['git', '-C', str(repo), 'show', 'HEAD:' + ev['path']], capture_output=True, text=True, encoding='utf-8', errors='replace')
                ok = shown.returncode == 0 and ev.get('contains', '') in shown.stdout
                if not ok:
                    bad += 1
                print('%-26s evidence %s: %s' % (c['key'], 'ok' if ok else 'MISSING', ev['path']))
    if bad:
        print('%d evidence item(s) do not hold at the project HEAD; nothing imported.' % bad)
        return 2
    if not a.apply:
        print('Check only: %d claims would be proposed to project "%s" as agent "%s". Add --apply.' % (len(doc['claims']), project, agent))
        return 0
    for c in doc['claims']:
        r = wiki.propose(agent, c['claim'], c['evidence'], project, subject=c.get('subject'), kind=c['kind'], watch_paths=c.get('watch_paths'), scope=c['scope'])
        print('%-26s %-13s %-17s %s%s' % (c['key'], r['status'], r['grade'], r['id'], '  (duplicate, not added)' if r.get('duplicate') else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
