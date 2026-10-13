import os

import pytest

from src.email_attachment_text import attachment_text
from src.clean_agent_preview import compact_schemas


def test_text_and_truncation(tmp_path):
    path = tmp_path / 'invoice.csv'
    path.write_text('Item,Amount\nServices,12345\n', encoding='utf-8')
    assert '12345' in attachment_text(path)['content']
    result = attachment_text(path, max_chars=10)
    assert len(result['content']) == 10
    assert 'truncated' in result['content_note']


def test_real_pdf_text(tmp_path):
    fitz = pytest.importorskip('fitz')  # PyMuPDF is in requirements-optional.txt
    path = tmp_path / 'payslip.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), 'Gross pay: 150000\nNet pay: 120000')
        doc.save(path)
    result = attachment_text(path)
    assert result['content_status'] == 'read'
    assert '120000' in result['content']
    assert 'Page 1' in result['content']


def test_blank_pdf_reports_ocr(tmp_path):
    from pypdf import PdfWriter
    path = tmp_path / 'scan.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    assert attachment_text(path)['content_status'] == 'needs_ocr'


def test_xlsx(tmp_path):
    Workbook = pytest.importorskip('openpyxl').Workbook  # requirements-optional.txt
    path = tmp_path / 'expenses.xlsx'
    book = Workbook()
    book.active.append(['Expenses', 12500])
    book.save(path)
    result = attachment_text(path)
    assert 'Expenses\t12500' in result['content']


def test_bad_pdf_reports_failure(tmp_path):
    path = tmp_path / 'bad.pdf'
    path.write_bytes(b'not a pdf')
    assert attachment_text(path)['content_status'] == 'failed'


def test_compact_live_alias_explains_reading():
    schema = {'type': 'function', 'function': {'name': 'mcp__email__download_attachment',
        'description': 'Download', 'parameters': {'type': 'object', 'properties': {}}}}
    description = compact_schemas([schema], model='Ajax')[0]['function']['description']
    assert 'before answering' in description
    assert 'contents inline' in description


def test_discovered_attachment_read_does_not_need_explicit_download_request():
    from src.clean_agent_preview import evaluate_preview_call
    decision = evaluate_preview_call('mcp__email__download_attachment',
        {'uid': '42', 'index': 0, 'account': 'fixture@example.test'},
        'What email had my latest payslip and how much')
    assert decision.allowed
    assert not evaluate_preview_call('write_file', {'path': '/tmp/test', 'content': 'x'},
                                    'What email had my latest payslip and how much').allowed


async def _live_attachment_check(monkeypatch):
    import json
    import os
    import src.clean_agent_preview as module
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    executions = []
    async def execute(block, **kwargs):
        name = block.tool_type.removeprefix('mcp__email__')
        executions.append(name)
        outputs = {
            'list_email_accounts': 'Account: fixture@example.test',
            'search_emails': 'UID: 42\nFolder: INBOX\nAccount: fixture@example.test\nSubject: September 2026 Payslip\nDate: 2026-09-25',
            'read_email': 'UID: 42\nFolder: INBOX\nAccount: fixture@example.test\nSubject: September 2026 Payslip\nBody: Your payslip is attached.\nAttachments: [0] payslip.pdf (application/pdf)',
            'download_attachment': 'Attachment content:\nPage 1:\nSeptember 2026 payslip\nGross pay: JPY 150000\nNet pay: JPY 120000',
        }
        return block.tool_type, {'exit_code': 0, 'stdout': outputs[name]}
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'list_email_accounts', 'search_emails', 'read_email', 'download_attachment'}]
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    chunks = [c async for c in module.stream_preview(
        endpoint_url=os.environ['ODYSSEUS_AJAX_TEST_URL'], model='Ajax', headers={},
        messages=[{'role': 'user', 'content': 'What email had my latest payslip and how much'}],
        turn_contract=contract, owner='fixture', session_id='fixture-attachment',
        disabled_tools=set(), tool_policy=policy, thinking_mode='off', max_rounds=6,
    )]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    answer = next((e['content'] for e in reversed(events) if e.get('type') == 'final_response'),
                  ''.join(e.get('delta', '') for e in events))
    assert 'download_attachment' in executions, (executions, answer)
    assert '120000' in answer.replace(',', ''), answer


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('ODYSSEUS_AJAX_TEST_URL'), reason='Opt-in Ajax fixture test')
async def test_live_ajax_reads_attachment(monkeypatch):
    await _live_attachment_check(monkeypatch)
