"""Run Claude Code, Codex and Hermes' MCP client at the same time against one scratch wiki, then verify.

    python run_all.py <scratch_dir>            (run it in the background; takes several minutes)
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
HERMES_PY = r'C:/Users/Protagonist/AppData/Local/hermes/installs/562e58e2ea9aca80/environments/6eff8cc60cb848909fbabec0b09c88c2/venv/Scripts/python.exe'
AGENT_SRC = r'C:\Users\Protagonist\AppData\Local\Odysseus\hermes\managed\webapp\releases\427ff712100d\src'


def main():
    scratch = Path(os.path.abspath(sys.argv[1]))
    if not scratch.drive:
        raise SystemExit('pass an absolute path with a drive letter (a rootless path resolves differently in each process)')
    only = sys.argv[2].split(',') if len(sys.argv) > 2 else ['claude', 'codex', 'hermes']
    shutil.rmtree(scratch, ignore_errors=True)
    subprocess.run([PY, '-I', str(HERE / 'common.py'), 'setup', str(scratch)], check=True, capture_output=True)
    env_json = str(scratch / 'env.json')
    results = {}

    def client(kind):
        t0 = time.time()
        try:
            if kind == 'hermes':
                henv = {k: v for k, v in os.environ.items() if k not in ('HERMES_HOME', 'PYTHONPATH', 'HERMES_WRITE_SAFE_ROOT')}
                henv['PYTHONPATH'] = AGENT_SRC
                p = subprocess.run([HERMES_PY, str(HERE / 'hermes_probe.py'), env_json], capture_output=True, text=True, env=henv, timeout=300, cwd=str(scratch))
                (scratch / 'out-hermes.json').write_text(p.stdout, encoding='utf-8')
                results[kind] = {'rc': p.returncode, 'seconds': round(time.time() - t0, 1), 'stderr': p.stderr[-300:]}
            else:
                p = subprocess.run([PY, '-I', str(HERE / 'run_client.py'), kind, env_json, '--timeout', '420'], capture_output=True, text=True, timeout=450)
                results[kind] = {'rc': p.returncode, 'seconds': round(time.time() - t0, 1), 'note': (p.stdout + p.stderr)[-300:]}
        except subprocess.TimeoutExpired:
            results[kind] = {'rc': 'timeout', 'seconds': round(time.time() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            results[kind] = {'rc': 'error', 'error': repr(exc)[:300]}

    threads = [threading.Thread(target=client, args=(k,)) for k in only]
    [t.start() for t in threads]
    [t.join() for t in threads]
    v = subprocess.run([PY, '-I', str(HERE / 'verify.py'), env_json], capture_output=True, text=True)
    summary = {'clients': results, 'verify': json.loads(v.stdout) if v.stdout.strip() else v.stderr[-500:]}
    (scratch / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
