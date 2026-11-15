import os, sys, socket, subprocess, time, urllib.request, json
from pathlib import Path
root=Path('/home/alteixeira20/Projects/Odysseus/worktrees/document-editor-remediation')
base=Path(__import__('tempfile').mkdtemp(prefix='document-editor-remediation-runtime-'))
base.mkdir(exist_ok=True)
data=base/'data'; data.mkdir(exist_ok=True)
with socket.socket() as s:
 s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
with socket.socket() as s:
 s.bind(('127.0.0.1',0)); chroma=s.getsockname()[1]
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',ODYSSEUS_DATA_DIR=str(data),DATABASE_URL='sqlite:///'+str(data/'app.db'),ODYSSEUS_BROWSER_MCP_CACHE=str(data/'browser-cache'),FASTEMBED_CACHE_PATH=str(base/'fastembed'),CHROMADB_HOST='127.0.0.1',CHROMADB_PORT=str(chroma),APP_PORT=str(port),APP_BIND='127.0.0.1',ODYSSEUS_ADMIN_USER='codex_audit',ODYSSEUS_ADMIN_PASSWORD=__import__('secrets').token_urlsafe(24),ODYSSEUS_NO_OPEN='1',ODYSSEUS_SKIP_RUN_HINT='1',ODYSSEUS_SKIP_ADMIN_PROMPT='1',ODYSSEUS_EMAIL_FIXTURE='1',HF_HUB_OFFLINE='1')
python='/tmp/document-editor-remediation-venv/bin/python'
status=1
with (base/'setup.log').open('w') as log:
 r=subprocess.run([python,'-c','import setup; setup.init_database(); result=setup.create_default_admin(); assert result in ("created", "exists"), result'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
print('isolated_setup_exit',r.returncode,flush=True)
if r.returncode: sys.exit(r.returncode)
with (base/'app.log').open('w') as log:
 p=subprocess.Popen([python,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
 try:
  ready=False
  for _ in range(40):
   if p.poll() is not None:break
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as resp:ready=resp.status==200
    if ready:break
   except Exception:pass
   time.sleep(.5)
  print('isolated_app_ready',ready,'exit',p.poll(),flush=True)
  if ready:
   r=subprocess.run([python,'-m','pytest','-p','no:cacheprovider','-q','tests/smoke/test_documents_smoke.py'],cwd=root,env=env)
   status=r.returncode
   print('documents_smoke_exit',r.returncode,flush=True)
  else:
   print((base/'app.log').read_text()[-5000:],flush=True)
 finally:
  if p.poll() is None:
   p.terminate()
   try:p.wait(timeout=8)
   except subprocess.TimeoutExpired:p.kill();p.wait()

sys.exit(status)
