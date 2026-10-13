"""Document tool follow-ups against disposable real SQLite storage."""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from src import database as compatibility_database
from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.document_tools import get_active_document, set_active_document


@pytest.fixture
def documents(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'documents.db'}")
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(compatibility_database, "SessionLocal", factory)
    saved_active = get_active_document()
    set_active_document(None)
    with factory() as db:
        db.add(database.Document(id="owned-document", owner="fixture-owner", title="Owned",
            language="text", current_content="First: alpha\nSecond: alpha\nKeep: violet-72"))
        db.add(database.Document(id="foreign-document", owner="other-owner", title="Foreign",
            language="text", current_content="Foreign alpha"))
        db.commit()
    yield
    set_active_document(saved_active)
    engine.dispose()


def read(document_id="owned-document", owner="fixture-owner"):
    return asyncio.run(TOOL_HANDLERS["manage_documents"](
        json.dumps({"action": "read", "document_id": document_id}), {"owner": owner}))


def edit(find, replacement, **ctx):
    return asyncio.run(TOOL_HANDLERS["edit_document"](
        f"<<<FIND>>>\n{find}\n<<<REPLACE>>>\n{replacement}\n<<<END>>>",
        {"owner": "fixture-owner", **ctx}))


def test_missing_explicit_target_does_not_edit_another_document(documents):
    before = read()
    result = edit("alpha", "WRONG", doc_id="deleted-document")
    assert "error" in result
    assert read() == before


def test_missing_explicit_target_does_not_replace_another_document(documents):
    before = read()
    result = asyncio.run(TOOL_HANDLERS["update_document"]("WRONG replacement", {
        "owner": "fixture-owner", "doc_id": "deleted-document"}))
    assert "error" in result
    assert read() == before


def test_missing_explicit_target_does_not_delete_another_document(documents):
    before = read()
    result = asyncio.run(TOOL_HANDLERS["manage_documents"](
        json.dumps({"action": "delete", "document_id": "deleted-document"}), {"owner": "fixture-owner"}))
    assert result.get("exit_code") == 1
    assert read() == before


@pytest.mark.parametrize("tool", ["edit_document", "update_document", "manage_documents"])
@pytest.mark.parametrize("target", ["deleted-document", "foreign-document"])
def test_unavailable_active_target_never_falls_back_to_other_document(documents, tool, target):
    before = read()
    foreign_before = read("foreign-document", "other-owner")
    set_active_document(target)
    content = {"edit_document": "<<<FIND>>>\nalpha\n<<<REPLACE>>>\nWRONG\n<<<END>>>",
               "update_document": "WRONG", "manage_documents": '{"action":"delete"}'}[tool]
    result = asyncio.run(TOOL_HANDLERS[tool](content, {"owner": "fixture-owner"}))
    assert result.get("exit_code") == 1
    assert read() == before
    assert read("foreign-document", "other-owner") == foreign_before


def test_targeted_edit_and_undo_preserve_other_occurrences(documents):
    before = read()
    result = edit("Second: alpha", "Second: beta", doc_id="owned-document")
    assert result["applied"] == 1 and result["skipped"] == 0
    assert read()["document"]["content"] == "First: alpha\nSecond: beta\nKeep: violet-72"
    assert "error" not in edit("Second: beta", "Second: alpha", doc_id="owned-document")
    assert read() == before


def test_no_target_legacy_fallback_still_scopes_to_owner(documents):
    set_active_document(None)
    result = edit("Second: alpha", "Second: beta")
    assert result["doc_id"] == "owned-document"
    assert "Second: beta" in read()["document"]["content"]


def test_sealed_missing_target_still_reports_version_guard(documents):
    before = read()
    result = edit("alpha", "WRONG", doc_id="deleted-document", expected_document_version=1)
    assert "error" in result
    assert read() == before


def test_delete_respects_dispatch_target_instead_of_global_active_document(documents):
    before = read()
    result = asyncio.run(TOOL_HANDLERS["manage_documents"]('{"action":"delete"}', {
        "owner": "fixture-owner", "doc_id": "deleted-document"}))
    assert result.get("exit_code") == 1
    assert read() == before


def test_delete_rejects_a_changed_sealed_document_version(documents):
    before = read()
    result = asyncio.run(TOOL_HANDLERS["manage_documents"](
        '{"action":"delete","document_id":"owned-document"}', {
            "owner": "fixture-owner", "doc_id": "owned-document", "expected_document_version": 99}))
    assert "error" in result
    assert read() == before


def test_valid_dispatch_delete_uses_its_target_and_matching_version(documents):
    foreign_before = read("foreign-document", "other-owner")
    result = asyncio.run(TOOL_HANDLERS["manage_documents"]('{"action":"delete"}', {
        "owner": "fixture-owner", "doc_id": "owned-document", "expected_document_version": 1}))
    assert result["exit_code"] == 0
    assert read()["exit_code"] == 1
    assert read("foreign-document", "other-owner") == foreign_before


def test_invalid_multi_edit_saves_only_exact_matches_and_reports_remainder(documents):
    result = asyncio.run(TOOL_HANDLERS["edit_document"](
        '<<<FIND>>>\nSecond: alpha\n<<<REPLACE>>>\nSecond: beta\n<<<END>>>\n'
        '<<<FIND>>>\nAbsent text\n<<<REPLACE>>>\nWrong\n<<<END>>>',
        {"owner": "fixture-owner", "doc_id": "owned-document"}))
    assert result['applied'] == 1 and result['partial'] is True
    assert result['invalid_edits'][0]['number'] == 2
    assert result['rejected'] == 1
    assert read()["document"]["content"] == "First: alpha\nSecond: beta\nKeep: violet-72"


def test_batch_with_only_bad_anchors_reports_all_without_saving(documents):
    blocks = [
        ('Imagined sentence', 'Corrected sentence'),
        ('alpha', 'gamma'),
        ('vio', 'violet'),
    ]
    content = ''.join(f'<<<FIND>>>\n{find}\n<<<REPLACE>>>\n{replace}\n<<<END>>>\n'
                      for find, replace in blocks)
    before = read()
    result = asyncio.run(TOOL_HANDLERS['edit_document'](content,
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert result['invalid_edit_numbers'] == [1, 2, 3]
    assert '#1 (0 matches)' in result['error']
    assert '#2 (2 matches)' in result['error']
    assert 'First: alpha' in result['error'] and 'Second: alpha' in result['error']
    assert read() == before


def test_long_proofreading_batch_saves_safe_matches_and_identifies_remainder(documents):
    from src.clean_agent_preview import preview_tool_result_text
    blocks = [(f'Keep: violet-{number}', f'Keep: violet-{number + 1}')
              for number in range(72, 82)]
    blocks.insert(4, ('Imagined sentence', 'Corrected sentence'))
    content = ''.join(f'<<<FIND>>>\n{find}\n<<<REPLACE>>>\n{replace}\n<<<END>>>\n'
                      for find, replace in blocks)
    result = asyncio.run(TOOL_HANDLERS['edit_document'](content,
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert result['partial'] is True
    assert result['applied'] == 10 and result['rejected'] == 1
    assert result['invalid_edits'][0]['number'] == 5
    assert read()['document']['content'].endswith('Keep: violet-82')
    feedback = preview_tool_result_text(result, 'edit_document', {})
    assert 'Retry only the rejected FIND entries' in feedback
    assert 'First: alpha' not in feedback


def test_inline_suggestion_is_reviewable_then_applies_only_its_target(documents):
    before = read()
    result = asyncio.run(TOOL_HANDLERS['suggest_document'](
        '<<<FIND>>>\nSecond: alpha\n<<<SUGGEST>>>\nSecond: beta\n<<<REASON>>>\nUse the corrected term.\n<<<END>>>',
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert 'error' not in result
    assert read() == before
    suggestion = result['suggestions'][0]
    applied = edit(suggestion['find'], suggestion['replace'], doc_id='owned-document')
    assert applied['applied'] == 1
    assert read()['document']['content'] == 'First: alpha\nSecond: beta\nKeep: violet-72'


def test_whole_document_update_persists_exact_replacement(documents):
    replacement = 'A complete rewritten document.\n\nWith a second paragraph.'
    result = asyncio.run(TOOL_HANDLERS['update_document'](replacement,
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert 'error' not in result
    assert read()['document']['content'] == replacement
    assert read('foreign-document', 'other-owner')['document']['content'] == 'Foreign alpha'


@pytest.mark.parametrize('find,replacement', [('alpha', 'beta'), ('vio', 'new'), ('tha', 'that')])
def test_ambiguous_or_partial_word_edits_do_not_mutate(documents, find, replacement):
    if find == 'tha':
        asyncio.run(TOOL_HANDLERS['update_document']('That is correct, and that stays.',
            {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    before = read()
    result = edit(find, replacement, doc_id='owned-document')
    assert result['exit_code'] == 1
    assert read() == before


def test_explicit_replace_all_corrects_every_occurrence(documents):
    from src.tool_schemas import function_call_to_tool_block
    block = function_call_to_tool_block('edit_document', {'edits': [
        {'find': 'alpha', 'replace': 'beta', 'replace_all': True}]})
    result = asyncio.run(TOOL_HANDLERS['edit_document'](block.content,
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert result.get('exit_code', 0) == 0 and not result.get('error')
    assert read()['document']['content'] == 'First: beta\nSecond: beta\nKeep: violet-72'


def test_replace_all_cannot_change_fragments_of_correct_words(documents):
    asyncio.run(TOOL_HANDLERS['update_document']('that banana being',
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    before = read()
    result = asyncio.run(TOOL_HANDLERS['edit_document'](
        '<<<FIND>>>\ntha\n<<<REPLACE_ALL>>>\nthat\n<<<END>>>',
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert result['exit_code'] == 1
    assert read() == before


def test_ambiguous_suggestion_returns_exact_recovery_anchors(documents):
    result = asyncio.run(TOOL_HANDLERS['suggest_document'](
        '<<<FIND>>>\nalpha\n<<<SUGGEST>>>\nbeta\n<<<REASON>>>\nClarify.\n<<<END>>>',
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert result['exit_code'] == 1
    assert 'First: alpha' in result['error']
    assert 'Second: alpha' in result['error']
    assert read()['document']['content'] == 'First: alpha\nSecond: alpha\nKeep: violet-72'


def test_mixed_suggestion_batch_queues_valid_items_and_reports_bad_anchors(documents):
    before = read()
    result = asyncio.run(TOOL_HANDLERS['suggest_document'](
        '<<<FIND>>>\nFirst: alpha\n<<<SUGGEST>>>\nFirst: beta\n<<<REASON>>>\nClarify.\n<<<END>>>\n'
        '<<<FIND>>>\nalpha\n<<<SUGGEST>>>\nbeta\n<<<REASON>>>\nClarify.\n<<<END>>>',
        {'owner': 'fixture-owner', 'doc_id': 'owned-document'}))
    assert result['count'] == 1 and result['partial'] is True
    assert result['invalid_suggestions'][0]['reason'] == 'ambiguous'
    assert result['suggestions'][0]['find'] == 'First: alpha'
    assert read() == before
