"""Malformed model arguments must not publish or rewrite a stored skill."""
import json

import pytest

from services.memory.skills import SkillsManager
from src.tools.system import do_manage_skills


@pytest.fixture
def owned_skills(tmp_path, monkeypatch):
    monkeypatch.setattr('src.constants.DATA_DIR', str(tmp_path))
    manager = SkillsManager(str(tmp_path))
    for owner in ('alice', 'bob'):
        manager.add_skill(name='owned-procedure', category=owner, owner=owner,
                          description='Owned fixture', procedure=['Check the result.'],
                          source='user', status='draft', confidence=.4)
    return manager, tmp_path


async def publish(confidence, owner='alice'):
    return await do_manage_skills(json.dumps({
        'action': 'publish', 'name': 'owned-procedure', 'confidence': confidence,
    }), owner=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('confidence', [
    'high', '', [.9], {}, 'NaN', 'Infinity', '-Infinity', '1e999', 10 ** 400,
])
async def test_invalid_publish_confidence_preserves_files_and_allows_correction(owned_skills, confidence):
    manager, root = owned_skills
    before = {p: p.read_bytes() for p in root.rglob('SKILL.md')}
    assert len(before) == 2

    result = await publish(confidence)

    assert result['exit_code'] == 1
    assert 'confidence' in result['error'] and 'finite number' in result['error']
    assert {p: p.read_bytes() for p in before} == before
    corrected = await publish(.75)
    assert 'error' not in corrected
    alice = manager.load(owner='alice')[0]
    bob = manager.load(owner='bob')[0]
    assert (alice['status'], alice['confidence']) == ('published', .75)
    assert (bob['status'], bob['confidence']) == ('draft', .4)


@pytest.mark.asyncio
@pytest.mark.parametrize(('confidence', 'expected'), [
    (.65, .65), ('0.7', .7), (0, 0), (1, 1), (-.2, 0), (1.2, 1), (None, .4),
])
async def test_publish_preserves_numeric_clamping_and_optional_confidence(owned_skills, confidence, expected):
    manager, _ = owned_skills

    result = await publish(confidence)

    assert 'error' not in result
    alice = manager.load(owner='alice')[0]
    assert (alice['status'], alice['confidence']) == ('published', expected)
    assert manager.load(owner='bob')[0]['status'] == 'draft'


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', ['carol', None])
async def test_publish_missing_owner_skill_keeps_existing_files(owned_skills, owner):
    _, root = owned_skills
    before = {p: p.read_bytes() for p in root.rglob('SKILL.md')}

    result = await publish(.75, owner=owner)

    assert result['exit_code'] == 1 and result['error']
    assert {p: p.read_bytes() for p in before} == before
