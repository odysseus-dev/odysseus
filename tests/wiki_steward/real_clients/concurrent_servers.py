"""Reproduce server crashes: N MCP server processes start at once against one wiki and each runs a short session.
Prints each process's exit status, stderr tail and replies. Used to find out why a client lost its stdio subprocess."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

env = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
agents = ['hermes-preview', 'claude-code', 'codex', 'wiki-bot']
results = {}


def session(i):
    agent = agents[i % len(agents)]
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'wiki_status', 'arguments': {'project': 'odysseus'}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'wiki_begin', 'arguments': {'project': 'odysseus', 'summary': 'concurrent %d' % i}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'wiki_propose', 'arguments': {
                'claim': 'Concurrent observation number %d from %s.' % (i, agent), 'project': 'odysseus', 'evidence': [{'type': 'note'}], 'subject': 'cc.%d' % i}}},
            {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'wiki_sync', 'arguments': {'project': 'odysseus'}}}]
    p = subprocess.run([env['python'], env['mcp_script']], input='\n'.join(json.dumps(m) for m in msgs) + '\n', capture_output=True, text=True,
                       env={**os.environ, 'WIKI_ROOT': env['wiki'], 'WIKI_AGENT': agent, 'PYTHONPATH': ''}, timeout=240)
    replies = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    results[i] = {'agent': agent, 'rc': p.returncode, 'replies': len(replies), 'errors': [r['id'] for r in replies if r.get('result', {}).get('isError')],
                  'stderr': p.stderr[-700:]}


threads = [threading.Thread(target=session, args=(i,)) for i in range(n)]
[t.start() for t in threads]
[t.join() for t in threads]
print(json.dumps(results, indent=1))
