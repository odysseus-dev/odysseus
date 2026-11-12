"""Run a real LLM client (Claude Code or Codex) against the Wiki Steward with an isolated, per-invocation MCP configuration.

    python run_client.py <claude|codex> <env.json> [--timeout 420]

Claude: --mcp-config <temp json> --strict-mcp-config (no global MCP servers), --no-session-persistence.
Codex:  -c mcp_servers.wiki.* overrides on the command line, --ephemeral, read-only sandbox. No config file is written.
The model's own account of what happened is NOT trusted; verify.py reads the wiki afterwards.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

CLIENTS = {'claude': ('claude-code', 'always works', 'claude'), 'codex': ('codex', 'always fails', 'codex')}
CODEX_JS = r'C:\Users\Protagonist\AppData\Roaming\npm\node_modules\@openai\codex\bin\codex.js'

PROMPT = """You are testing a shared wiki through the MCP server named "wiki". Use ONLY the wiki tools. Do these steps in order, one tool call each, then stop:
1. wiki_status with project "odysseus".
2. wiki_begin with project "odysseus" and summary "{name} probe task". Remember the task_id it returns.
3. wiki_propose with project "odysseus", scope "source", subject "probe.{name}.source", claim "src/cookbook.py handles read-only files on Windows ({name}).", evidence [{{"type":"file","path":"src/cookbook.py","contains":"handles read-only files"}}].
4. wiki_propose with project "odysseus", subject "probe.conflict", claim "The {name} agent says the cookbook delete {opinion} on Windows.", evidence [{{"type":"note","text":"{name} says so"}}].
5. wiki_propose with project "other" (any claim text of at least 10 characters, empty evidence). It is expected to be refused. Do not retry it.
6. Check whether a tool named wiki_decide exists. Do not invent it. If it does not exist, say so.
7. wiki_record_task with project "odysseus", summary "{name} probe done", task_id equal to the task_id from step 2, status "done".
8. wiki_sync with project "odysseus".
Finish with one line beginning RESULT: that lists, for each step number, ok / refused / missing."""


def main():
    kind, env_path = sys.argv[1], Path(sys.argv[2])
    timeout = int(sys.argv[sys.argv.index('--timeout') + 1]) if '--timeout' in sys.argv else 420
    env = json.loads(env_path.read_text(encoding='utf-8'))
    agent, opinion, name = CLIENTS[kind]
    prompt = PROMPT.format(name=name, opinion=opinion)
    scratch = Path(env['scratch'])
    work = scratch / ('cwd-' + kind)
    work.mkdir(exist_ok=True)
    server_env = {'WIKI_ROOT': env['wiki'], 'WIKI_AGENT': agent, 'PYTHONPATH': ''}
    started = time.time()
    if kind == 'claude':
        cfg = scratch / 'claude-mcp.json'
        cfg.write_text(json.dumps({'mcpServers': {'wiki': {'command': env['python'], 'args': [env['mcp_script']], 'env': server_env}}}), encoding='utf-8')
        cmd = ['claude', '-p', prompt, '--mcp-config', str(cfg), '--strict-mcp-config', '--allowedTools', 'mcp__wiki', '--model', 'haiku',
               '--max-turns', '20', '--output-format', 'stream-json', '--verbose', '--no-session-persistence']
    else:
        py = env['python'].replace('\\', '/')
        script = env['mcp_script'].replace('\\', '/')
        envtoml = '{' + ', '.join('%s="%s"' % (k, v.replace('\\', '/')) for k, v in server_env.items()) + '}'
        cmd = ['node', CODEX_JS, 'exec', '--skip-git-repo-check', '--ephemeral', '--json', '-s', 'read-only', '-C', str(work),
               '-c', 'mcp_servers.wiki.command="%s"' % py, '-c', 'mcp_servers.wiki.args=["%s"]' % script,
               '-c', 'mcp_servers.wiki.env=' + envtoml, '-c', 'mcp_servers.wiki.startup_timeout_sec=60', '-c', 'mcp_servers.wiki.tool_timeout_sec=120',
               '-c', 'approval_policy="never"', prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=str(work), timeout=timeout)
    (scratch / ('out-%s.jsonl' % kind)).write_text(proc.stdout, encoding='utf-8')
    (scratch / ('err-%s.txt' % kind)).write_text(proc.stderr, encoding='utf-8')
    print(json.dumps({'client': kind, 'agent': agent, 'rc': proc.returncode, 'seconds': round(time.time() - started, 1), 'stdout_bytes': len(proc.stdout)}))


if __name__ == '__main__':
    main()
