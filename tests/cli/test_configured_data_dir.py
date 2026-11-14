"""Real CLI processes must read/write the configured app store, not a checkout."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ('preset', 'memory', 'skills', 'personal', 'theme', 'research', 'cookbook', 'backup')


def seed_store(root, marker):
    def write(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    write('presets.json', {'fixture': {'name': 'fixture', 'system_prompt': marker}})
    write('memory.json', [{'id': 'fixture', 'text': marker, 'timestamp': 1}])
    write('user_prefs.json', {'_users': {'fixture': {'theme': {'name': marker}}}})
    write('cookbook_state.json', {'env': {'fixture_marker': marker}, 'tasks': []})
    write('deep_research/fixture.json', {'query': marker, 'status': 'done', 'sources': []})
    skill = root / 'skills/general/fixture/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text(f'---\nname: fixture\ndescription: {marker}\ncategory: general\n---\n\n{marker}\n')
    personal = root / 'personal_docs' / f'{marker}.txt'
    personal.parent.mkdir()
    personal.write_text(marker)
    (root / 'fixture-marker.txt').write_text(marker)


@pytest.fixture
def stores(tmp_path):
    checkout = tmp_path / 'checkout'
    scripts = checkout / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copytree(ROOT / 'scripts/_lib', scripts / '_lib', ignore=shutil.ignore_patterns('__pycache__'))
    for tool in TOOLS:
        shutil.copy2(ROOT / 'scripts' / f'odysseus-{tool}', scripts)
    configured = tmp_path / 'configured store'
    legacy = tmp_path / 'legacy data override'
    for directory, marker in [(configured, 'configured-store'), (checkout / 'data', 'checkout-decoy'), (legacy, 'legacy-decoy')]:
        seed_store(directory, marker)
    env = dict(os.environ, ODYSSEUS_DATA_DIR=str(configured), DATA_DIR=str(legacy),
               PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1', LOG_LEVEL='ERROR')

    def cli(tool, *args, expected_code=0):
        result = subprocess.run([sys.executable, str(scripts / f'odysseus-{tool}'), *args],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == expected_code, result.stderr
        return json.loads(result.stdout) if expected_code == 0 else result

    return configured, checkout / 'data', legacy, cli


@pytest.mark.parametrize('tool', TOOLS)
def test_cli_reads_configured_store_from_another_working_directory(stores, tmp_path, tool):
    configured, checkout, _, cli = stores
    if tool == 'backup':
        archive_path = tmp_path / 'snapshot.tar.gz'
        cli(tool, 'snapshot', '--out', str(archive_path), '--include-research')
        assert not (checkout.parent / 'backups').exists()
        with tarfile.open(archive_path) as archive:
            names = archive.getnames()
            assert all(name.startswith('data/') for name in names)
            observed = archive.extractfile('data/fixture-marker.txt').read().decode()
    elif tool == 'preset':
        # Preset list intentionally returns metadata only, so read the prompt.
        observed = cli(tool, 'get', 'fixture')
    elif tool == 'theme':
        observed = cli(tool, 'get', '--user', 'fixture')
    elif tool == 'cookbook':
        # Ignore the unrelated live tmux-session inventory.
        observed = cli(tool, 'list')['state']
    else:
        observed = cli(tool, 'list')
    text = json.dumps(observed)
    assert 'configured-store' in text
    assert 'checkout-decoy' not in text and 'legacy-decoy' not in text
    assert configured.is_dir()


@pytest.mark.parametrize('tool,args,path', [
    ('preset', ('set', 'fixture', '--prompt', 'updated prompt'), 'presets.json'),
    ('research', ('delete', 'fixture'), 'deep_research/fixture.json'),
    ('skills', ('delete', 'fixture'), 'skills/general/fixture/SKILL.md'),
])
def test_cli_writes_leave_checkout_and_legacy_stores_untouched(stores, tool, args, path):
    configured, checkout, legacy, cli = stores
    old_configured = (configured / path).read_bytes()
    old_checkout = (checkout / path).read_bytes()
    old_legacy = (legacy / path).read_bytes()
    cli(tool, *args)
    if tool == 'preset':
        assert json.loads((configured / path).read_text())['fixture']['system_prompt'] == 'updated prompt'
        assert (configured / path).read_bytes() != old_configured
    else:
        assert not (configured / path).exists()
    assert (checkout / path).read_bytes() == old_checkout
    assert (legacy / path).read_bytes() == old_legacy


def test_personal_directory_tracking_uses_personal_docs_subdirectory(stores, tmp_path):
    configured, checkout, legacy, cli = stores
    documents = tmp_path / 'documents'
    documents.mkdir()
    (documents / 'note.txt').write_text('owned fixture')
    cli('personal', 'add-dir', str(documents))
    expected = configured / 'personal_docs/indexed_directories.json'
    assert json.loads(expected.read_text()) == [str(documents)]
    assert not (configured / 'indexed_directories.json').exists()
    assert not (checkout / 'indexed_directories.json').exists()
    assert not (legacy / 'indexed_directories.json').exists()


def test_backup_restores_portable_data_archive_into_configured_basename(stores, tmp_path):
    configured, checkout, _, cli = stores
    archive_path = tmp_path / 'portable.tar.gz'
    with tarfile.open(archive_path, 'w:gz') as archive:
        body = b'portable restored fixture'
        member = tarfile.TarInfo('data/nested/restored.txt')
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    old_checkout = (checkout / 'fixture-marker.txt').read_bytes()
    result = cli('backup', 'restore', str(archive_path), '--yes')
    assert (configured / 'nested/restored.txt').read_bytes() == body
    assert not (configured / 'fixture-marker.txt').exists()
    stash = Path(result['previous_data_stashed_at'])
    assert stash.parent == configured.parent and stash.name.startswith(configured.name + '.before-restore-')
    assert (stash / 'fixture-marker.txt').read_text() == 'configured-store'
    assert (checkout / 'fixture-marker.txt').read_bytes() == old_checkout


def test_backup_rejects_output_inside_configured_store(stores):
    configured, _, _, cli = stores
    result = cli('backup', 'snapshot', '--out', str(configured / 'self.tar.gz'), expected_code=1)
    assert 'output path must be outside' in result.stderr
    assert not (configured / 'self.tar.gz').exists()
