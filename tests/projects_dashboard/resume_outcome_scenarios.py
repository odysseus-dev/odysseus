"""Synthetic interrupted transcripts -> what the resumed model is actually shown. Pure functions only; nothing is executed or written.

Run by test_resume_tool_outcomes.py under the Hermes interpreter: argv[1] = patched Hermes source, argv[2] = scratch HERMES_HOME."""
import json, os, sys
sys.path.insert(0, sys.argv[1]); os.environ.setdefault('HERMES_HOME', sys.argv[2])
from agent.replay_cleanup import canonicalize_replay_history
from agent.agent_runtime_helpers import sanitize_api_messages
from gateway.run import build_resume_recovery_note


def call(i, name, args):
    return {'id': i, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def res(i, name, content, **kw):
    return {'role': 'tool', 'tool_call_id': i, 'name': name, 'content': content, **kw}


U = {'role': 'user', 'content': 'implement the change and commit it'}
OK = lambda i, n: res(i, n, json.dumps({'ok': True}))
SCEN = {
    'file_write_no_result': [U, {'role': 'assistant', 'content': '', 'tool_calls': [call('w1', 'write_file', {'path': 'F:/p/a.py', 'content': 'x'})]}],
    'git_commit_no_result': [U, {'role': 'assistant', 'content': '', 'tool_calls': [call('t1', 'terminal', {'command': 'git commit -am "feat: x"'})]}],
    'command_no_exit_status': [U, {'role': 'assistant', 'content': '', 'tool_calls': [call('t2', 'terminal', {'command': 'npm run build'})]},
                               res('t2', 'terminal', json.dumps({'output': 'building...\n[Command interrupted]', 'exit_code': 130}))],
    'partial_block_3_calls': [U, {'role': 'assistant', 'content': '', 'tool_calls': [call('a', 'read_file', {'path': 'F:/p/a.py'}), call('b', 'write_file', {'path': 'F:/p/b.py', 'content': 'y'}), call('c', 'terminal', {'command': 'git commit -am "feat: y"'})]},
                             OK('a', 'read_file')],
    'readonly_no_result': [U, {'role': 'assistant', 'content': '', 'tool_calls': [call('r1', 'read_file', {'path': 'F:/p/a.py'})]}],
    'all_answered': [U, {'role': 'assistant', 'content': '', 'tool_calls': [call('a', 'read_file', {'path': 'F:/p/a.py'})]}, OK('a', 'read_file'), {'role': 'assistant', 'content': 'done'}],
    'unidentifiable_call': [U, {'role': 'assistant', 'content': '', 'tool_calls': [{'type': 'function', 'function': {'arguments': '{}'}}, call('k', 'terminal', {'command': 'echo hi'})]}, OK('k', 'terminal')],
}


def run_all():
    out = {}
    for name, hist in SCEN.items():
        canon = canonicalize_replay_history(hist)
        wire = sanitize_api_messages([dict(m) for m in canon])
        tail = [{'role': m['role'], 'id': m.get('tool_call_id'), 'disposition': m.get('effect_disposition'), 'content': m.get('content') if isinstance(m.get('content'), str) else json.dumps(m.get('content'))}
                for m in wire[2:]]
        try:
            note = build_resume_recovery_note('restart_timeout', 'continue', interactive=True, history=canon)
        except TypeError:
            note = build_resume_recovery_note('restart_timeout', 'continue', interactive=True)
        out[name] = {'tail': tail, 'note': note}
    return out


if __name__ == '__main__':
    print('@@' + json.dumps(run_all()))
