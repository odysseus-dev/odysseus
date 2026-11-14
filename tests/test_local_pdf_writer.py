import asyncio
import json
import unicodedata
from pathlib import Path

import pytest

pymupdf = pytest.importorskip('pymupdf')

from src.agent_tools.pdf_writer import render_pdf, publish_new_pdf
from src.agent_tools.filesystem_tools import WriteFileTool
from src.tool_schemas import function_call_to_tool_block


def test_render_pdf_multiple_pages_and_escape_external_html(tmp_path):
    content = '# Verification\n' + '\n'.join(f'Line {i}: Native tool execution and PDF creation are working.' for i in range(140)) + '\n<img src="file:///etc/passwd">'
    result = render_pdf(content)
    pdf = pymupdf.open(stream=result, filetype='pdf')
    text = unicodedata.normalize('NFKC', ''.join(page.get_text() for page in pdf))
    assert len(pdf) > 1
    assert 'Verification' in text and 'Line 139' in text
    assert '<img src=' in text
    assert not any(page.get_images() for page in pdf)


def test_publish_does_not_replace_existing_artifact(tmp_path):
    target = tmp_path / 'existing.pdf'
    target.write_bytes(b'existing')
    with pytest.raises(FileExistsError):
        publish_new_pdf(str(target), render_pdf('New document'))
    assert target.read_bytes() == b'existing'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['existing.pdf']


@pytest.mark.skipif(not Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf').is_file(),
                    reason='checks the native macOS Unicode font')
def test_macos_pdf_preserves_multilingual_text():
    text = 'English café\n中文 日本語 한국어\nПривет мир\nمرحبا بالعالم'
    with pymupdf.open(stream=render_pdf(text), filetype='pdf') as pdf:
        extracted = unicodedata.normalize('NFKC', pdf[0].get_text())
    for line in text.splitlines():
        assert line in extracted


def test_model_arguments_preserve_explicit_pdf_format():
    block = function_call_to_tool_block('write_file', json.dumps({'path':'example.pdf', 'content':'Hello', 'format':'pdf'}))
    assert json.loads(block.content)['format'] == 'pdf'


@pytest.mark.asyncio
async def test_writer_creates_pdf_and_preserves_binary_guard(tmp_path, monkeypatch):
    import src.tool_execution as execution
    monkeypatch.setattr(execution, '_resolve_tool_path', lambda p: str(tmp_path / p))
    monkeypatch.setattr(execution, '_display_tool_path', lambda p: Path(p).name)
    result = await WriteFileTool().execute(json.dumps({'path':'example.pdf','content':'# Hello\nWorld','format':'pdf'}), {})
    assert result['exit_code'] == 0
    with pymupdf.open(tmp_path / 'example.pdf') as pdf:
        assert 'Hello' in pdf[0].get_text() and 'World' in pdf[0].get_text()
    rejected = await WriteFileTool().execute(json.dumps({'path':'example.pdf','content':'fake PDF'}), {})
    assert rejected['exit_code'] != 0
    with pymupdf.open(tmp_path / 'example.pdf') as pdf:
        assert 'Hello' in pdf[0].get_text()

@pytest.mark.asyncio
async def test_pdf_full_dispatch_preserves_workspace_and_effect_claim(tmp_path, monkeypatch):
    from src import tool_execution
    from src.agent_runtime.authority import OperationGrant, RequestAuthority
    from src.agent_runtime.journal import ActionJournal, bind_journal
    from src.agent_runtime.effect_log import EffectLog
    from src.agent_runtime.effects import Predicate
    from src.tool_capabilities import ToolRunSecurityContext
    from src.tool_types import ToolBlock

    ws = tmp_path / 'workspace'
    ws.mkdir()
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    authority = RequestAuthority('pdf-test', 'alice', 'thread', str(ws), (OperationGrant('write_file'),))
    journal = ActionJournal(workspace=str(ws), observed_artifacts=('actual.pdf',))
    journal.effects = EffectLog(journal.run_id, directory=tmp_path / 'effects')
    with bind_journal(journal):
        _, result = await tool_execution.execute_tool_block(
            ToolBlock('write_file', json.dumps({'path': 'actual.pdf', 'content': '# Hello\nPDF test', 'format':'pdf'})),
            owner='alice', session_id='thread', workspace=str(ws),
            security_context=ToolRunSecurityContext(), request_authority=authority)
    assert result['exit_code'] == 0, result
    with pymupdf.open(ws / 'actual.pdf') as pdf:
        assert 'PDF test' in pdf[0].get_text()
    assert journal.effects.history().claims[0].obligations[0].predicate is Predicate.EXISTS


def test_pdf_only_authority_does_not_grant_general_file_writes(tmp_path):
    from src.agent_runtime.authority import create_request_authority, ExactOperation
    auth = create_request_authority('Create a PDF', owner='alice', session_id='s', workspace=str(tmp_path))
    valid = ExactOperation.normalize('write_file', json.dumps({'path':'file.pdf','content':'Hello','format':'pdf'}))
    ordinary = ExactOperation.normalize('write_file', json.dumps({'path':'other.txt','content':'Hello'}))
    assert auth.permits(valid)
    assert not auth.permits(ordinary)
    read_only = create_request_authority('Summarize this PDF', owner='alice', session_id='s', workspace=str(tmp_path))
    assert not read_only.permits(valid)
    refused = create_request_authority("Do not create a PDF; just explain it", owner='alice', session_id='s', workspace=str(tmp_path))
    assert not refused.permits(valid)
    summarize = create_request_authority('Read the PDF and write a summary', owner='alice', session_id='s', workspace=str(tmp_path))
    assert not summarize.permits(valid)


def test_interactive_pdf_policy_requires_request_and_format():
    from src.clean_agent_preview import evaluate_preview_call
    pdf = {'path':'hello.pdf','content':'Hello','format':'pdf'}
    assert evaluate_preview_call('write_file', pdf, 'Create a PDF').allowed
    assert not evaluate_preview_call('write_file', pdf, 'Summarize a PDF').allowed
    assert not evaluate_preview_call('write_file', {'path':'hello.txt','content':'Hello'}, 'Create a PDF').allowed
    assert not evaluate_preview_call('write_file', {**pdf,'path':'hello.txt'}, 'Create a PDF').allowed

@pytest.mark.asyncio
async def test_compact_stream_executes_pdf_writer_with_real_dispatch(tmp_path, monkeypatch):
    import src.clean_agent_preview as preview
    from src import tool_execution
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    args = {'path': 'example.pdf', 'format': 'pdf', 'content': '# Verification\nCreated by the agent.'}
    rounds = iter([
        {'choices': [{'delta': {'tool_calls': [{'index':0, 'id':'pdf-1', 'type':'function', 'function':{'name':'write_file', 'arguments': json.dumps(args)}}]}}]},
        {'choices': [{'delta': {'content':'Created example.pdf.'}}]},
    ])
    requests = []
    class Response:
        def __init__(self, payload): self.payload=payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(rounds))
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'write_file']
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    chunks = [c async for c in preview.stream_preview(endpoint_url='http://test', model='llama3.1:8b',
        headers={}, messages=[{'role':'user','content':'Create a PDF example in the workspace'}],
        turn_contract=contract, session_id='s', owner='alice', workspace=str(tmp_path),
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=3)]
    assert (tmp_path/'example.pdf').exists(), chunks
    with pymupdf.open(tmp_path/'example.pdf') as pdf:
        assert 'Created by the agent.' in pdf[0].get_text()
    schema = next(t for t in requests[0]['tools'] if t['function']['name']=='write_file')
    assert 'format' in schema['function']['parameters']['required']
    assert not any('No changes were made' in c for c in chunks)

@pytest.mark.asyncio
async def test_webui_pdf_request_offers_writer_in_compact_contract(monkeypatch, context_probe_ledger):
    from tests.test_context_resolution_route import _drive_route, _install_model, _spy
    import src.tool_security as security
    monkeypatch.setattr(security, 'blocked_tools_for_owner', lambda owner: set())
    seen = _spy(monkeypatch)
    _install_model(monkeypatch)
    await _drive_route(monkeypatch, message='Create a PDF in the agent workspace with the text Hello')
    kwargs = seen['preview_kwargs'][-1]
    assert 'write_file' in kwargs['turn_contract'].offered, (kwargs['turn_contract'].unavailable, kwargs['turn_contract'].capabilities, kwargs['turn_contract'].active_capabilities)

@pytest.mark.asyncio
async def test_pdf_with_untrusted_context_requires_exact_approval(tmp_path, monkeypatch):
    import src.clean_agent_preview as preview
    from src import tool_execution
    from src.tool_approvals import tool_approval_store
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    args = {'path': 'approval.pdf', 'format': 'pdf', 'content': 'Approval test'}
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'pdf','type':'function','function':{'name':'write_file','arguments':json.dumps(args)}}]}}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'write_file']
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    chunks = [c async for c in preview.stream_preview(endpoint_url='http://test', model='llama3.1:8b',
        headers={}, messages=[{'role':'user','content':'Create a PDF in the workspace'}],
        turn_contract=contract, session_id='approval-s', owner='alice', workspace=str(tmp_path),
        disabled_tools=set(), tool_policy=ToolPolicy(), external_untrusted_context_seen=True, max_rounds=3)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: {')]
    ask = next(e['data'] for e in events if e.get('type') == 'ask_user')
    assert ask['kind'] == 'tool_approval'
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['tool_events'][0]['ask_user']['approval_id'] == ask['approval_id']
    assert not (tmp_path/'approval.pdf').exists()
    pending = tool_approval_store.peek(ask['approval_id'])
    assert pending.tool_name == 'write_file' and json.loads(pending.content)['format'] == 'pdf'
    from src.tool_types import ToolBlock
    from src.tool_capabilities import ToolRunSecurityContext
    exact = tool_approval_store.consume(ask['approval_id'], decision='approve', owner='alice', session_id='approval-s')
    _, result = await tool_execution.execute_tool_block(
        ToolBlock(pending.tool_name, pending.content), owner='alice', session_id='approval-s',
        workspace=str(tmp_path), security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
        exact_approval=exact, request_authority=pending.request_authority)
    assert result['exit_code'] == 0, result
    with pymupdf.open(tmp_path/'approval.pdf') as pdf:
        assert 'Approval test' in pdf[0].get_text()

@pytest.mark.asyncio
async def test_pdf_dispatch_refuses_outside_workspace_and_symlink(tmp_path, monkeypatch):
    from src import tool_execution
    from src.agent_runtime.authority import create_request_authority
    from src.tool_capabilities import ToolRunSecurityContext
    from src.tool_types import ToolBlock
    ws = tmp_path / 'workspace'
    ws.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (ws/'escape').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    auth = create_request_authority('Create a PDF in the workspace', owner='alice', session_id='s', workspace=str(ws))
    for path in ('../outside/escape.pdf', 'escape/escape.pdf'):
        _, result = await tool_execution.execute_tool_block(
            ToolBlock('write_file', json.dumps({'path':path,'format':'pdf','content':'Hello'})),
            owner='alice', session_id='s', workspace=str(ws),
            security_context=ToolRunSecurityContext(), request_authority=auth)
        assert result.get('exit_code') != 0 or result.get('blocked')
    assert list(outside.iterdir()) == []


def test_pdf_renderer_refuses_empty_and_oversized_text():
    from src.agent_tools.pdf_writer import MAX_TEXT_BYTES
    with pytest.raises(ValueError): render_pdf('')
    with pytest.raises(ValueError): render_pdf('a' * (MAX_TEXT_BYTES + 1))

@pytest.mark.asyncio
async def test_webui_route_creates_pdf_through_agent_and_completion_gate(monkeypatch, context_probe_ledger):
    from tests.test_context_resolution_route import _drive_route, _install_model, _ModelStream
    import src.tool_security as security
    from src import tool_execution
    monkeypatch.setattr(security, 'blocked_tools_for_owner', lambda owner: set())
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    args = json.dumps({'path':'webui-probe.pdf','format':'pdf','content':'Hello from the agent.'})
    first = _ModelStream(['data: '+json.dumps({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'pdf','type':'function','function':{'name':'write_file','arguments':args}}]}}]}), 'data: [DONE]'])
    second = _ModelStream(['data: '+json.dumps({'choices':[{'delta':{'content':'Created webui-probe.pdf.'}}]}), 'data: [DONE]'])
    _install_model(monkeypatch, first, second)
    _, chunks = await _drive_route(monkeypatch, message='Create a PDF named webui-probe.pdf in the agent workspace containing Hello from the agent.')
    output = Path(tool_execution.default_agent_workspace())/'webui-probe.pdf'
    assert output.exists(), chunks
    with pymupdf.open(output) as pdf: assert 'Hello from the agent.' in pdf[0].get_text()
    assert not any('no matching artifact mutation' in str(c) for c in chunks)
    assert not any('No changes were made' in str(c) for c in chunks)


def test_pdf_continuation_only_shortcuts_standalone_requests():
    from src.agent_tools.pdf_writer import single_pdf_request, pdf_creation_requested
    assert pdf_creation_requested('Use write_file to create ocean-sample-26.pdf containing Ocean')
    assert not pdf_creation_requested('Write a summary of report.pdf')
    assert single_pdf_request('Create a PDF with a title and a sentence')
    assert single_pdf_request('Create a PDF named auto-check-2026.pdf with the title "Verification check"')
    assert not single_pdf_request('Create a PDF and then email it')
    assert not single_pdf_request('Create a PDF and verify it')
    assert not single_pdf_request('Create a PDF and a spreadsheet')
    assert not single_pdf_request('Summarize this PDF')


def test_approved_simple_pdf_finishes_without_duplicate_tool_call(tmp_path, monkeypatch):
    from tests.test_agent_rounds_exhausted import _patch_common, _collect, _types
    import src.agent_loop as al
    from src import tool_execution
    from src.agent_runtime.authority import create_request_authority
    from src.tool_approvals import ToolApprovalStore
    from src.tool_capabilities import capabilities_for_action
    from src.tool_policy import ToolPolicy
    _patch_common(monkeypatch)
    from src import tool_utils
    from src.upload_handler import UploadHandler
    monkeypatch.setattr(tool_utils, '_upload_handler', UploadHandler(str(tmp_path), str(tmp_path / 'uploads')))
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    monkeypatch.setattr(al, 'execute_tool_block', tool_execution.execute_tool_block)
    monkeypatch.setattr(al, 'blocked_tools_for_owner', lambda owner: set())
    async def no_model(*args, **kwargs):
        raise AssertionError('A completed approved PDF should not be sent to the model again')
        yield
    monkeypatch.setattr(al, 'stream_llm_with_fallback', no_model)
    request = 'Create a PDF named result.pdf in the workspace with the title Hello'
    content = json.dumps({'path':'result.pdf','format':'pdf','content':'# Hello'})
    authority = create_request_authority(request, owner='alice', session_id='pdf-s', workspace=str(tmp_path))
    store = ToolApprovalStore()
    pending = store.create(owner='alice', session_id='pdf-s', origin_run_id='r', tool_name='write_file',
        content=content, workspace=str(tmp_path), external_untrusted_context_seen=True,
        capabilities=capabilities_for_action('write_file',content), request_text=request,
        continuation_query=request, selected_tools=['write_file'], request_authority=authority)
    exact = store.consume(pending.approval_id,decision='approve_task',owner='alice',session_id='pdf-s')
    events = _types(_collect(al.stream_agent_loop('http://test/v1','llama3.1:8b',
        [{'role':'user','content':request}], owner='alice', session_id='pdf-s', workspace=str(tmp_path),
        exact_approval=exact, request_authority=authority, tool_policy=ToolPolicy(), relevant_tools={'write_file'}, max_rounds=3)))
    outputs = [e for e in events if e.get('type')=='tool_output']
    assert len(outputs)==1 and outputs[0]['exit_code']==0, events
    assert (tmp_path/'result.pdf').is_file()
    assert any(e.get('type')=='final_response' and '[Download PDF](/api/upload/' in e.get('content','') for e in events), [(e.get('type'), e.get('content'), e.get('output')) for e in events if e.get('type') in ('final_response','tool_output')]


@pytest.mark.asyncio
async def test_pdf_download_registered_for_owner(tmp_path, monkeypatch):
    from src import tool_execution, tool_utils
    from src.upload_handler import UploadHandler
    handler = UploadHandler(str(tmp_path), str(tmp_path / 'uploads'))
    monkeypatch.setattr(tool_utils, '_upload_handler', handler)
    monkeypatch.setattr(tool_execution, '_resolve_tool_path', lambda p: str(tmp_path / p))
    monkeypatch.setattr(tool_execution, '_display_tool_path', lambda p: Path(p).name)
    result = await WriteFileTool().execute(json.dumps({'path':'download.pdf','content':'Hello','format':'pdf'}), {'owner':'alice'})
    assert result['exit_code'] == 0, result
    upload_id = result['download_url'].rsplit('/', 1)[-1]
    row = next(row for row in handler._load_upload_index().values() if row.get('id') == upload_id)
    assert row['owner'] == 'alice'
    assert (tmp_path / 'download.pdf').read_bytes().startswith(b'%PDF')
    assert '[Download PDF](' in result['output']

@pytest.mark.asyncio
async def test_specific_fresh_chat_pdf_request_offers_writer(monkeypatch, context_probe_ledger):
    from tests.test_context_resolution_route import _drive_route, _install_model, _spy
    import src.tool_security as security
    monkeypatch.setattr(security, 'blocked_tools_for_owner', lambda owner: set())
    seen = _spy(monkeypatch)
    _install_model(monkeypatch)
    await _drive_route(monkeypatch, message='Create a PDF named ocean-sample-26.pdf with the title Ocean and the sentence This is a sample PDF.')
    kwargs = seen['preview_kwargs'][-1]
    assert 'write_file' in kwargs['turn_contract'].offered, (kwargs['turn_contract'].unavailable, kwargs['turn_contract'].capabilities, kwargs['turn_contract'].active_capabilities)


def test_mistral_envelope_recovery_requires_offered_well_formed_calls():
    from src.clean_agent_preview import recover_mistral_tool_calls
    offered = [{'function': {'name': 'write_file'}}]
    good = '[TOOL_CALLS] [{"name":"write_file","arguments":{"path":"a.pdf","content":"Hi","format":"pdf"}}]'
    assert len(recover_mistral_tool_calls(good, 'mistral:7b', offered, 1)) == 1
    assert len(recover_mistral_tool_calls(good.removeprefix('[TOOL_CALLS] '), 'mistral:7b', offered, 1)) == 1
    for bad in ('Here is an example: ' + good, good + ' trailing',
                '[TOOL_CALLS] [{"name":"python","arguments":{"code":"x"}}]',
                '[TOOL_CALLS] [{"name":"write_file","arguments":{}} , {"name":"bad","arguments":{}}]'):
        assert not recover_mistral_tool_calls(bad, 'mistral:7b', offered, 1)
    assert not recover_mistral_tool_calls(good, 'llama3.1:8b', offered, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('marker', ['[TOOL_CALLS] ', ''])
async def test_mistral_envelope_executes_pdf_writer_through_preview(tmp_path, monkeypatch, marker):
    import src.clean_agent_preview as preview
    from src import tool_execution, tool_utils
    from src.upload_handler import UploadHandler
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    monkeypatch.setattr(tool_utils, '_upload_handler', UploadHandler(str(tmp_path), str(tmp_path/'uploads')))
    call={'name':'write_file','arguments':{'path':'mistral-test.pdf','content':'Ocean','format':'pdf'}}
    text=marker+json.dumps([call, call])
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':text}}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    schemas=[s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name']=='write_file']
    contract=resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy())
    chunks=[c async for c in preview.stream_preview(endpoint_url='http://test',model='mistral:7b',headers={},
        messages=[{'role':'user','content':'Create a PDF named mistral-test.pdf with the title Ocean'}],
        turn_contract=contract,session_id='s',owner='alice',workspace=str(tmp_path),disabled_tools=set(),tool_policy=ToolPolicy(),max_rounds=3)]
    assert (tmp_path/'mistral-test.pdf').is_file(), chunks
    assert any('[Download PDF]' in c for c in chunks), chunks
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: {')]
    tool_outputs = [e for e in events if e.get('type') == 'tool_output']
    assert len(tool_outputs) == 1 and not tool_outputs[0].get('error'), tool_outputs
    assert not any('[TOOL_CALLS]' in e.get('delta', '') for e in events), events
    assert any(e.get('type') == 'final_response' and e.get('replacement_scope') == 'turn' and '[Download PDF]' in e.get('content', '') and '[TOOL_CALLS]' not in e.get('content', '') for e in events), events
