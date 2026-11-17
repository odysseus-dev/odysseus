from src.clean_agent_preview import email_draft_document_id, evaluate_preview_call


def test_compact_runtime_allows_contact_resolution():
    decision = evaluate_preview_call('resolve_contact', {'name': 'Jon'}, 'Write an email to Jon')
    assert decision.allowed, decision.reason


def test_successful_email_receipt_opens_its_document():
    doc_id = '0ca3b68b-667c-487b-b3ec-676afe932c28'
    result = {'stdout': f'Created Odysseus email draft (document ID: {doc_id}).'}
    assert email_draft_document_id('mcp__email__draft_email', result) == doc_id
    assert email_draft_document_id('draft_email', result, failed=True) is None
    assert email_draft_document_id('read_email', result) is None
    assert email_draft_document_id('draft_email', {'stdout': 'Error: failed'}) is None
