"""Per-file deletion must use the scanner's snapshot-relative path contract."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from routes.cookbook_helpers import _cached_model_scan_script

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


def delete_command(model, files, *, windows=False, mode='files', expected_refresh=None):
    if not NODE:
        pytest.skip('node is not available')
    source = (ROOT / 'static/js/cookbookServe.js').read_text(encoding='utf-8')
    functions = []
    for begin, end in [
        ('function _ggufFilesForModel(', '\nfunction _runnableGgufFiles('),
        ('function _safeGgufRelPath(', '\nfunction _ggufDeleteChoice('),
        ('function _shellPathExpr(', '\nfunction _selectedGgufExpr('),
        ('async function _deleteCachedModel(', '\nasync function _promptResumeIncompleteModel('),
    ]:
        start = source.index(begin)
        functions.append(source[start:source.index(end, start)])
    payload = json.dumps({'model': model, 'files': files, 'windows': windows, 'mode': mode})
    harness = '''
const input=JSON.parse(process.argv[1]), commands=[], errors=[], refreshes=[];
let _cachedAllModels=[];
const _resolveCacheHost=()=>'', _isWindows=()=>input.windows;
const _shellQuote=s=>"'"+String(s).replace(/'/g,"'\\\\''")+"'";
const _ggufDeleteChoice=async()=>({mode:input.mode,files:input.files});
const uiModule={styledConfirm:async()=>true,showError:e=>errors.push(e)};
const _fetchCachedModels=async fresh=>{refreshes.push(fresh)};
const _fetchCookbookWithTimeout=async(url,opts)=>{commands.push(JSON.parse(opts.body).command);return {ok:true}};
'''
    harness += '\n'.join(functions)
    harness += '''
(async()=>{await _deleteCachedModel('fixture/model',null,false,input.model);
if(errors.length)throw Error(JSON.stringify(errors));
console.log(JSON.stringify({commands,refreshes}));})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([NODE, '-e', harness, payload], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    if expected_refresh is not None:
        assert value["refreshes"] == [expected_refresh]
    return value["commands"]


def model(path, local=False):
    return {'repo_id': 'fixture/model', 'path': str(path), 'is_local_dir': local,
            'gguf_files': [{'rel_path': 'revision/selected.gguf'}, {'rel_path': 'revision/keep.gguf'}]}


@pytest.mark.skipif(os.name == 'nt', reason='executes generated POSIX delete command')
@pytest.mark.parametrize('local', [False, True], ids=['hf-snapshot', 'custom-model-dir'])
def test_real_generated_command_removes_selected_file_and_keeps_sibling(tmp_path, local):
    cache = tmp_path / 'cache with spaces'
    base = cache / ('fixture/model' if local else 'models--fixture--model/snapshots')
    directory = base / 'revision'
    directory.mkdir(parents=True)
    selected, keep = directory / 'selected.gguf', directory / 'keep.gguf'
    selected.write_bytes(b'owned selected fixture')
    keep.write_bytes(b'owned sibling fixture')
    commands = delete_command(model(cache, local), [{'rel_path': 'revision/selected.gguf'}])
    assert len(commands) == 1
    result = subprocess.run(commands[0], shell=True, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert not selected.exists(), commands[0]
    assert keep.read_bytes() == b'owned sibling fixture'


@pytest.mark.parametrize('windows,local,expected', [
    (False, False, '/cache/models--fixture--model/snapshots/revision/nested/model.gguf'),
    (True, False, r'C:\cache\models--fixture--model\snapshots\revision\nested\model.gguf'),
    (True, True, r'C:\cache\fixture\model\revision\nested\model.gguf'),
])
def test_hf_and_local_target_paths_for_each_shell(windows, local, expected):
    path = r'C:\cache' if windows else '/cache'
    commands = delete_command(model(path, local), [{'rel_path': 'revision/nested/model.gguf'}], windows=windows)
    assert len(commands) == 1 and expected in commands[0]
    if local:
        assert 'snapshots' not in commands[0]


def test_default_hf_location_also_uses_snapshot_root():
    record = model('unused')
    del record['path']
    command = delete_command(record, [{'rel_path': 'revision/selected.gguf'}])[0]
    assert '/models--fixture--model/snapshots/revision/selected.gguf' in command


def test_whole_repo_delete_keeps_repository_root():
    command = delete_command(model('/cache'), [], mode='repo')[0]
    assert command == 'rm -rf "/cache/models--fixture--model"'
    assert 'snapshots' not in command


def test_traversal_selection_never_sends_a_shell_command():
    assert delete_command(model('/cache'), [{'rel_path': '../foreign.gguf'}]) == []


def test_windows_separators_from_inventory_are_normalized_for_unix():
    command = delete_command(model('/cache'), [{'rel_path': r'revision\nested\selected.gguf'}])[0]
    assert '/snapshots/revision/nested/selected.gguf' in command


def test_scanner_reports_paths_relative_to_snapshots_directory(tmp_path):
    cache = tmp_path / 'hub'
    directory = cache / 'models--fixture--model/snapshots/revision/nested'
    directory.mkdir(parents=True)
    (directory / 'model-Q4_K_M.gguf').write_bytes(b'owned fixture')
    # Execute the real generated scanner definitions against only this fixture.
    definitions = _cached_model_scan_script().split('for _hf_cache in hf_cache_paths():')[0]
    scope = {}
    exec(compile(definitions, '<cache scanner>', 'exec'), scope)
    scope['scan_hf'](str(cache))
    record = scope['models'][0]
    assert record['path'] == str(cache) and not record.get('is_local_dir')
    assert record['gguf_files'][0]['rel_path'] == 'revision/nested/model-Q4_K_M.gguf'


def test_per_file_delete_forces_new_scan_instead_of_reviving_cached_inventory():
    delete_command(model('/cache'), [{'rel_path': 'revision/selected.gguf'}], expected_refresh=True)
