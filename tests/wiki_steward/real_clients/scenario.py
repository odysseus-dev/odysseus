"""End-to-end scenario with the real clients against a wiki that already has a valid steward.json.

    python scenario.py --wiki-root <wiki> --out <scratch dir> [--project selftest] [--wait 75]

  1. Claude Code (LLM, haiku): inbox, status, search, begin a 1-minute task, propose a source claim, try a forbidden project,
     hand off to Hermes, then STOP without recording the task (an interrupted session).
  2. wait until the task has expired.
  3. Hermes' MCP client: connecting triggers the automatic recovery pass; then inbox, ack, search, propose a contradicting
     claim and a decision claim, hand back to Claude Code, read the reconcile report.
  4. Claude Code again: read the automatic "Session interrupted" handoff, ack, complete the task by id, sync.
  5. verify from the wiki itself (not from what any model says).

Writes only to the given wiki's <project> and to <out>. Use a scratch clone first; run it on the real wiki only on purpose.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import wiki_steward as ws  # noqa: E402

PY = sys.executable
HPY = r'C:/Users/Protagonist/AppData/Local/hermes/installs/562e58e2ea9aca80/environments/6eff8cc60cb848909fbabec0b09c88c2/venv/Scripts/python.exe'
AGENT_SRC = r'C:\Users\Protagonist\AppData\Local\Odysseus\hermes\managed\webapp\releases\427ff712100d\src'
SCRIPT = str(ROOT / 'scripts' / 'wiki_mcp.py')

P1 = """Use ONLY the MCP server named "wiki". The project is "{p}". Do these steps in order, one tool call each, and do not repeat a call:
1. wiki_inbox.
2. wiki_status for the project.
3. wiki_search with query "wiki steward class Wiki" for the project.
4. wiki_begin for the project with summary "claude scenario task" and expect_minutes 1. Remember the task_id.
5. wiki_propose for the project with scope "source", subject "{p}.wiki-class", claim "src/wiki_steward.py defines the class Wiki.", evidence [{{"type":"file","path":"src/wiki_steward.py","contains":"class Wiki:"}}]. Remember the id.
6. wiki_propose with project "no-such-project" (any claim text of 10+ characters, empty evidence). It is expected to be refused. Do not retry.
7. wiki_handoff to "hermes" for the project with summary "Please review the Wiki class claim", next_steps ["search for it"], refs {{"claims": ["<id from step 5>"]}}.
Then STOP. Do NOT call wiki_record_task or wiki_sync: this session ends early on purpose.
Finish with one line starting RESULT: giving the task_id and the claim id."""

P3 = """Use ONLY the MCP server named "wiki". The project is "{p}". Do these steps in order, one tool call each:
1. wiki_inbox. Read every handoff.
2. Find the handoff whose summary starts with "Session interrupted". Its refs.tasks[0] is the id of your unfinished task.
3. wiki_ack every handoff you read (one call per handoff id).
4. wiki_conflicts for the project.
5. wiki_record_task for the project with summary "claude scenario task completed", status "done" and task_id equal to the id from step 2.
6. wiki_sync for the project.
7. wiki_status for the project.
Finish with one line starting RESULT: giving the task_id you completed."""


def claude(prompt, env, out, tag, agent='claude-code'):
    cfg = out / ('claude-%s.json' % tag)
    cfg.write_text(json.dumps({'mcpServers': {'wiki': {'command': PY, 'args': [SCRIPT], 'env': {'WIKI_ROOT': env['wiki'], 'WIKI_AGENT': agent, 'PYTHONPATH': ''}}}}), encoding='utf-8')
    cmd = ['claude', '-p', prompt, '--mcp-config', str(cfg), '--strict-mcp-config', '--allowedTools', 'mcp__wiki', '--model', 'haiku', '--max-turns', '30',
           '--output-format', 'stream-json', '--verbose', '--no-session-persistence']
    work = out / 'cwd-claude'
    work.mkdir(exist_ok=True)
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=str(work), timeout=420)
    (out / ('claude-%s.jsonl' % tag)).write_text(p.stdout, encoding='utf-8')
    calls, errors = [], 0
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for block in ((ev.get('message') or {}).get('content') or []):
            if isinstance(block, dict) and block.get('type') == 'tool_use' and str(block.get('name', '')).startswith('mcp__wiki__'):
                calls.append(block['name'].split('__')[-1])
            if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('is_error'):
                errors += 1
    return {'rc': p.returncode, 'seconds': round(time.time() - t0, 1), 'calls': calls, 'tool_errors': errors}


def hermes(steps, env, out, tag, agent='hermes'):
    cfg, st = out / ('hermes-%s-cfg.json' % tag), out / ('hermes-%s-steps.json' % tag)
    cfg.write_text(json.dumps({'python': PY, 'mcp_script': SCRIPT, 'wiki': env['wiki'], 'agent': agent}), encoding='utf-8')
    st.write_text(json.dumps(steps), encoding='utf-8')
    henv = {k: v for k, v in os.environ.items() if k not in ('HERMES_HOME', 'PYTHONPATH', 'HERMES_WRITE_SAFE_ROOT')}
    henv['PYTHONPATH'] = AGENT_SRC
    p = subprocess.run([HPY, str(HERE / 'hermes_session.py'), str(cfg), str(st)], capture_output=True, text=True, env=henv, cwd=str(out), timeout=300)
    (out / ('hermes-%s.json' % tag)).write_text(p.stdout, encoding='utf-8')
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {'rc': p.returncode, 'raw': p.stdout[-400:], 'stderr': p.stderr[-600:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wiki-root', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--project', default='selftest')
    ap.add_argument('--wait', type=int, default=75)
    a = ap.parse_args()
    out, proj = Path(os.path.abspath(a.out)), a.project
    if not out.drive or not Path(a.wiki_root).is_absolute():
        raise SystemExit('absolute paths with a drive letter are required')
    out.mkdir(parents=True, exist_ok=True)
    env = {'wiki': str(Path(a.wiki_root).resolve())}
    wiki = ws.Wiki(env['wiki'])
    report = {'wiki': env['wiki'], 'project': proj, 'integrity_before': wiki.integrity()}
    errors, _ = wiki.validate_config()
    report['config_errors'] = errors
    if errors or report['integrity_before']:
        print(json.dumps(report, indent=1))
        raise SystemExit('refusing to run against an invalid or tampered wiki')
    report['s1_claude'] = claude(P1.format(p=proj), env, out, 'session1')
    time.sleep(a.wait)
    hsteps = [{'tool': 'wiki_inbox'},
              {'tool': 'wiki_search', 'args': {'query': 'src/wiki_steward.py defines the class Wiki', 'project': proj}},
              {'tool': 'wiki_propose', 'args': {'claim': 'The class Wiki is not defined in src/wiki_steward.py; it lives in src/legacy_wiki.py instead.', 'project': proj,
                                                 'subject': proj + '.wiki-class', 'evidence': [{'type': 'note', 'text': 'hermes believes this'}]}},
              {'tool': 'wiki_propose', 'args': {'claim': 'Decision: src/wiki_steward.py is the single write path for the wiki.', 'project': proj, 'kind': 'decision', 'scope': 'source',
                                                 'subject': proj + '.single-write-path', 'evidence': [{'type': 'file', 'path': 'src/wiki_steward.py', 'contains': 'class Wiki:'}]}},
              {'tool': 'wiki_propose', 'args': {'claim': 'Hermes attempts to read the secrets file.', 'project': proj, 'evidence': [{'type': 'file', 'path': '.env', 'contains': 'KEY'}]}},
              {'tool': 'wiki_decide', 'args': {'claim_id': 'c-x', 'decision': 'accept'}},
              {'tool': 'wiki_resolve', 'args': {'claim_id': 'c-x', 'decision': 'accept'}},
              {'tool': 'wiki_conflicts', 'args': {'project': proj}},
              {'tool': 'wiki_reconcile', 'args': {'project': proj}}]
    h1 = hermes(hsteps, env, out, 'session2')
    inbox = next((r['data'] for r in h1.get('results', []) if r['tool'] == 'wiki_inbox'), None)
    ack_steps = [{'tool': 'wiki_ack', 'args': {'handoff_id': h['id']}} for h in (inbox or {}).get('result', []) if h.get('from') == 'claude-code']
    ack_steps.append({'tool': 'wiki_handoff', 'args': {'to': 'claude-code', 'project': proj, 'summary': 'Found a contradicting claim and a decision that needs review',
                                                       'next_steps': ['resolve your interrupted task'], 'open_questions': ['is the Wiki class still in wiki_steward.py?']}})
    report['s2_hermes'] = {'visible': h1.get('tools_visible'), 'steps': [(r['tool'], 'hidden' if not r['visible'] else 'error' if r['error'] else 'ok') for r in h1.get('results', [])]}
    report['s2b_hermes_ack'] = [(r['tool'], 'error' if r['error'] else 'ok') for r in hermes(ack_steps, env, out, 'session2b').get('results', [])]
    report['s3_claude'] = claude(P3.format(p=proj), env, out, 'session3')
    report['verify'] = verify(wiki, proj)
    (out / 'scenario-report.json').write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print(json.dumps(report, indent=1, default=str))


def verify(wiki, proj):
    claims, tasks = wiki._claims(proj), [t for t in wiki._tasks(proj) if not t['id'].startswith('git')]
    handoffs = [h for h in wiki._handoffs() if h['project'] == proj]
    audit = [json.loads(l) for l in (wiki.root / 'audit' / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    return {'integrity_after': wiki.integrity(),
            'claims': [{'by': c['agents'][0], 'subject': c.get('subject'), 'kind': c['kind'], 'status': c['status'], 'grade': c['grade']} for c in claims],
            'tasks': [{'agent': t['agent'], 'status': t['status'], 'summary': t['summary'][:45]} for t in tasks],
            'handoffs': [{'from': h['from'], 'to': h['to'], 'auto': h.get('auto', False), 'status': h['status'], 'summary': h['summary'][:50]} for h in handoffs],
            'audit_actors': sorted({a['actor'] for a in audit}), 'decide_actions': [a['target'] for a in audit if a['action'] == 'decide'],
            'sensitive_verified': [c['claim'][:40] for c in claims if c['kind'] != 'fact' and c['status'] == 'verified'],
            'claims_in_other_projects': {p: len(wiki._claims(p)) for p in wiki.config['projects'] if p != proj}}


if __name__ == '__main__':
    main()
