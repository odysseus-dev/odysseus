"""Update-guard behaviour inside a patched Hermes tree. Calls detection and preflight functions only: no updater, no git, no network.
argv[1] = patched Hermes source, argv[2] = scratch HERMES_HOME."""
import io, json, os, sys, tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1]); os.environ['HERMES_HOME'] = sys.argv[2]
os.environ.pop('HERMES_MANAGED', None)
from unittest import mock
from hermes_cli import odysseus_managed as om
from hermes_cli import update_contract as uc
import hermes_cli.main as hm

tmp = Path(tempfile.mkdtemp(prefix='updguard_'))
def tree(name, marker=None, layout=False, git=False):
    root = tmp / name / ('releases/abc123/src' if layout else 'src')
    root.mkdir(parents=True)
    if layout: (root.parent / 'release.json').write_text('{"state":"ready"}')
    if git: (root / '.git').mkdir()
    if marker is not None: (root / om.MARKER_NAME).write_text(marker, encoding='utf-8')
    return root
SHIPPED = Path(sys.argv[1]) / om.MARKER_NAME
trees = {
    'shipped_marker': tree('a', SHIPPED.read_text(encoding='utf-8'), git=True),
    'marker_bad_json': tree('b', '{not json', git=True),
    'marker_wrong_owner': tree('c', '{"managed_by": "someone-else"}', git=True),
    'marker_empty': tree('d', '', git=True),
    'marker_missing_in_managed_layout': tree('e', layout=True, git=True),
    'independent_git_install': tree('f', git=True),
    'plain_dir': tree('g'),
}
out = {'states': {k: om.managed_state(v) for k, v in trees.items()}, 'missing_path': om.managed_state(tmp / 'nope')}
out['refusals'] = {k: (None if (r := uc.odysseus_managed_refusal(v)) is None else {'code': r.code, 'msg': r.message}) for k, v in trees.items()}

def preflight(root, **flags):
    args = SimpleNamespace(check=False, plan=False, list_venv_holders=False, **flags)
    buf = io.StringIO(); code = None; result = None; reached = []
    with mock.patch.object(hm, 'PROJECT_ROOT', root), redirect_stdout(buf), \
         mock.patch('hermes_cli.update_channel.handle_metadata_args', lambda *a, **k: False), \
         mock.patch('hermes_cli.update_contract.evaluate_update_admission', lambda r: reached.append('admission') or None), \
         mock.patch('hermes_cli.update_cmd._cmd_update_check', lambda **k: reached.append('check')), \
         mock.patch('hermes_cli.config.is_managed', lambda: False):
        try: result = hm._update_preflight_handled(args)
        except SystemExit as e: code = e.code
    return {'exit': code, 'handled': result, 'text': buf.getvalue()[:300], 'reached': reached}
out['preflight_apply_managed'] = preflight(trees['shipped_marker'])
out['preflight_apply_invalid'] = preflight(trees['marker_bad_json'])
out['preflight_apply_layout_only'] = preflight(trees['marker_missing_in_managed_layout'])
out['preflight_check_managed'] = preflight(trees['shipped_marker'], ) if False else None
chk = SimpleNamespace(check=True, plan=False, list_venv_holders=False)
reached = []; buf = io.StringIO(); code = None
with mock.patch.object(hm, 'PROJECT_ROOT', trees['shipped_marker']), redirect_stdout(buf), \
     mock.patch('hermes_cli.update_channel.handle_metadata_args', lambda *a, **k: False), \
     mock.patch('hermes_cli.update_contract.evaluate_update_admission', lambda r: None), \
     mock.patch('hermes_cli.update_cmd._cmd_update_check', lambda **k: reached.append('check')), \
     mock.patch('hermes_cli.config.is_managed', lambda: False):
    try: res = hm._update_preflight_handled(chk)
    except SystemExit as e: res, code = None, e.code
out['preflight_check_managed'] = {'exit': code, 'handled': res, 'reached': reached, 'text': buf.getvalue()[:120]}
out['preflight_apply_independent'] = preflight(trees['independent_git_install'])
out['message'] = om.REFUSAL_MESSAGE
out['marker_shipped_file'] = json.loads(SHIPPED.read_text(encoding='utf-8'))['managed_by']
print('@@' + json.dumps(out))
