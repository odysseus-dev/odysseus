#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the Wiki Steward to an agent (Hermes, Claude Code, Codex).

Started by the agent's own MCP client for the length of its session; nothing runs in the background. The agent's identity
comes from the WIKI_AGENT environment variable in that agent's MCP entry, never from tool arguments. Reviewer-only
operations (decide, adopt, revert) are deliberately not exposed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import wiki_steward as ws  # noqa: E402

S = {'type': 'string'}
L = {'type': 'array', 'items': S}
TOOLS = {
    'wiki_search': ('search', 'Search verified wiki knowledge. Results carry freshness; STALE entries must be re-verified before use.',
                    {'query': S, 'project': S, 'k': {'type': 'integer'}, 'include_provisional': {'type': 'boolean'}}, ['query']),
    'wiki_propose': ('propose', 'Submit a claim with evidence ({type: commit, ref} | {type: file, path, contains} | {type: note}).',
                     {'claim': S, 'project': S, 'subject': S, 'kind': S, 'evidence': {'type': 'array', 'items': {'type': 'object'}}, 'watch_paths': L},
                     ['claim', 'project', 'evidence']),
    'wiki_verify': ('verify', 'Re-run verification of a claim, optionally adding evidence.', {'proposal_id': S, 'evidence': {'type': 'array', 'items': {'type': 'object'}}}, ['proposal_id']),
    'wiki_record_task': ('record_task', 'Record completed work.', {'project': S, 'summary': S, 'commits': L, 'files': L, 'tests': L, 'status': S}, ['project', 'summary']),
    'wiki_sync': ('sync', 'Record new commits of the project repository as task logs (mechanical, idempotent). Call at the end of a task.', {'project': S}, ['project']),
    'wiki_handoff': ('handoff', 'Prepare a handoff for another agent.', {'to': S, 'project': S, 'summary': S, 'next_steps': L, 'open_questions': L, 'refs': {'type': 'object'}}, ['to', 'project', 'summary']),
    'wiki_status': ('status', 'Verified current status of a project.', {'project': S}, ['project']),
    'wiki_conflicts': ('conflicts', 'Contradictory, outdated, unverified and review-pending knowledge.', {'project': S}, []),
    'wiki_inbox': ('inbox', 'Open handoffs addressed to you.', {}, []),
    'wiki_ack': ('ack', 'Acknowledge a handoff.', {'handoff_id': S}, ['handoff_id']),
}


def handle(wiki, agent, msg):
    method, mid = msg.get('method'), msg.get('id')
    if mid is None:
        return None
    if method == 'initialize':
        result = {'protocolVersion': msg.get('params', {}).get('protocolVersion', '2025-03-26'), 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'wiki-steward', 'version': '0.1.0'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': n, 'description': d, 'inputSchema': {'type': 'object', 'properties': p, 'required': r}}
                            for n, (_, d, p, r) in TOOLS.items()]}
    elif method == 'tools/call':
        name, args = msg['params']['name'], msg['params'].get('arguments') or {}
        try:
            if name not in TOOLS:
                raise ws.Denied('unknown tool')
            out, error = getattr(wiki, TOOLS[name][0])(agent, **args), False
        except (ws.WikiError, TypeError) as exc:
            out, error = {'error': type(exc).__name__, 'message': str(exc)}, True
        result = {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False)}], 'isError': error}
    elif method == 'ping':
        result = {}
    else:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'method not found'}}
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def main():
    wiki, agent = ws.Wiki(os.environ['WIKI_ROOT']), os.environ['WIKI_AGENT']
    for line in sys.stdin:
        if line.strip():
            reply = handle(wiki, agent, json.loads(line))
            if reply is not None:
                sys.stdout.write(json.dumps(reply) + '\n')
                sys.stdout.flush()


if __name__ == '__main__':
    main()
