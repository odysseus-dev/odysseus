import json
import pytest

from src.ollama_structured_tools import generate_proposal


@pytest.fixture
def tool_request():
    return {'model':'test-model', 'messages':[{'role':'user','content':'Create a PDF.'}],
            'tools':[{'type':'function','function':{'name':'write_file','description':'Create a file',
                     'parameters':{'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'},
                        'format':{'type':'string','enum':['pdf']}},'required':['path','content','format'],
                        'additionalProperties':False}}}], 'max_tokens':512}


class Response:
    def __init__(self, proposal): self.proposal = proposal
    def raise_for_status(self): pass
    def json(self): return {'message':{'content':json.dumps(self.proposal)}}


class Client:
    def __init__(self, proposal): self.proposal=proposal; self.sent=None
    async def post(self,url,json): self.sent=(url,json); return Response(self.proposal)


@pytest.mark.asyncio
async def test_valid_proposal_becomes_native_delta(tool_request):
    client=Client({'name':'write_file','arguments':{'path':'a.pdf','content':'hello','format':'pdf'}})
    delta=await generate_proposal(client,tool_request)
    assert client.sent[0]=='http://127.0.0.1:11434/api/chat'
    call=delta['tool_calls'][0]['function']
    assert call['name']=='write_file' and json.loads(call['arguments'])['format']=='pdf'


@pytest.mark.asyncio
@pytest.mark.parametrize('proposal', [
    {'name':'python','arguments':{'code':'danger'}},
    {'name':'write_file','arguments':{'path':'a.pdf','content':'hello','format':'text'}},
    {'name':'write_file','arguments':{'path':'a.pdf','content':'hello','format':'pdf','extra':1}},
])
async def test_invalid_or_unoffered_proposal_never_becomes_call(tool_request,proposal):
    with pytest.raises(ValueError): await generate_proposal(Client(proposal),tool_request)


@pytest.mark.asyncio
async def test_answer_does_not_create_a_tool_call(tool_request):
    delta=await generate_proposal(Client({'name':'__answer__','arguments':{'content':'Need details.'}}),tool_request)
    assert delta == {'content':'Need details.'}


@pytest.mark.asyncio
async def test_required_choice_disallows_answer(tool_request):
    tool_request['tool_choice']='required'
    with pytest.raises(ValueError): await generate_proposal(Client({'name':'__answer__','arguments':{'content':'No'}}),tool_request)


@pytest.mark.asyncio
async def test_preview_adapter_dispatches_through_normal_writer(tmp_path, monkeypatch):
    import src.clean_agent_preview as preview
    from src import ollama_tool_detection, tool_execution, tool_utils
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    from src.upload_handler import UploadHandler
    monkeypatch.setattr(ollama_tool_detection, 'native_tool_capability', lambda *args: False)
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    monkeypatch.setattr(tool_utils, '_upload_handler', UploadHandler(str(tmp_path),str(tmp_path/'uploads')))
    proposal={'name':'write_file','arguments':{'path':'samantha.pdf','content':'Ocean','format':'pdf'}}
    class MockClient(Client):
        def __init__(self, **kwargs): super().__init__(proposal)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    monkeypatch.setattr(preview.httpx, 'AsyncClient', MockClient)
    schemas=[s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name']=='write_file']
    contract=resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy())
    chunks=[c async for c in preview.stream_preview(endpoint_url='http://127.0.0.1:11434/v1/chat/completions',
        model='samantha-mistral:latest',headers={},messages=[{'role':'user','content':'Create a PDF named samantha.pdf with Ocean'}],
        turn_contract=contract,session_id='s',owner='alice',workspace=str(tmp_path),disabled_tools=set(),tool_policy=ToolPolicy(),max_rounds=3)]
    assert (tmp_path/'samantha.pdf').is_file(), chunks
    assert any('[Download PDF]' in c for c in chunks), chunks


@pytest.mark.asyncio
async def test_repeated_tool_call_is_retried_and_answered(tool_request):
    args={'path':'a.pdf','content':'hello','format':'pdf'}
    tool_request['messages'] += [
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'write_file','arguments':json.dumps(args)}}]},
        {'role':'tool','tool_call_id':'one','content':'Created file.'},
    ]
    class SequenceClient(Client):
        def __init__(self): self.items=iter([{'name':'write_file','arguments':args},
                                             {'name':'__answer__','arguments':{'content':'The file is ready.'}}]);self.calls=0
        async def post(self,url,json): self.calls+=1; return Response(next(self.items))
    client=SequenceClient(); delta=await generate_proposal(client,tool_request)
    assert client.calls==2 and delta=={'content':'The file is ready.'}


@pytest.mark.asyncio
async def test_repeated_tool_call_is_never_dispatched_again(tool_request):
    args={'path':'a.pdf','content':'hello','format':'pdf'}
    tool_request['messages'] += [
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'write_file','arguments':json.dumps(args)}}]},
        {'role':'tool','tool_call_id':'one','content':'Created file.'},
    ]
    result=await generate_proposal(Client({'name':'write_file','arguments':args}),tool_request)
    assert 'tool_calls' not in result and 'repeating' in result['content']


@pytest.mark.asyncio
async def test_simple_read_returns_actual_tool_result_without_another_call(tool_request):
    tool_request['messages'] = [
        {'role':'user','content':'Read example.pdf and tell me its title and sentence.'},
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'read_file','arguments':'{"path":"example.pdf"}'}}]},
        {'role':'tool','tool_call_id':'one','content':'[Page 1 text]:\nTitle: Ocean\nThis is a sample PDF.'},
    ]
    class NoCall:
        async def post(self,*args,**kwargs): raise AssertionError('Read result should be returned directly')
    result=await generate_proposal(NoCall(),tool_request)
    assert 'Ocean' in result['content'] and 'This is a sample PDF.' in result['content']


@pytest.mark.asyncio
async def test_old_turn_result_is_not_reused(tool_request):
    tool_request['messages'] = [
        {'role':'user','content':'Read old.pdf and tell me its title and sentence.'},
        {'role':'assistant','tool_calls':[{'id':'old','function':{'name':'read_file','arguments':'{"path":"old.pdf"}'}}]},
        {'role':'tool','tool_call_id':'old','content':'Old data'},
        {'role':'user','content':'Read new.pdf and tell me its title and sentence.'},
    ]
    client=Client({'name':'write_file','arguments':{'path':'a.pdf','content':'hello','format':'pdf'}})
    result=await generate_proposal(client,tool_request)
    assert result.get('tool_calls') and 'Old data' not in str(result)


@pytest.mark.asyncio
async def test_failed_read_is_not_returned_as_success(tool_request):
    tool_request['messages'] = [
        {'role':'user','content':'Read example.pdf and tell me its title and sentence.'},
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'read_file','arguments':'{"path":"example.pdf"}'}}]},
        {'role':'tool','tool_call_id':'one','content':'{"exit_code":1,"error":"not found"}'},
    ]
    client=Client({'name':'__answer__','arguments':{'content':'The file was not found.'}})
    result=await generate_proposal(client,tool_request)
    assert result['content']=='The file was not found.'


@pytest.mark.asyncio
async def test_preview_reads_current_file_without_reusing_old_turn(tmp_path, monkeypatch):
    import pymupdf
    import src.clean_agent_preview as preview
    from src import ollama_tool_detection, tool_execution
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72,72), 'Title: Ocean\nThis is a sample PDF.')
        pdf.save(tmp_path/'current.pdf')
    monkeypatch.setattr(ollama_tool_detection, 'native_tool_capability', lambda *args: False)
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    proposal={'name':'read_file','arguments':{'path':'current.pdf'}}
    class MockClient(Client):
        def __init__(self, **kwargs): super().__init__(proposal)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    monkeypatch.setattr(preview.httpx, 'AsyncClient', MockClient)
    schemas=[s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name']=='read_file']
    contract=resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy())
    messages=[{'role':'user','content':'Read old.pdf and tell me its title and sentence.'},
              {'role':'assistant','tool_calls':[{'id':'old','function':{'name':'read_file','arguments':'{"path":"old.pdf"}'}}]},
              {'role':'tool','tool_call_id':'old','content':'{"exit_code":1,"error":"old failure"}'},
              {'role':'user','content':'Read current.pdf and tell me its title and sentence.'}]
    from src.agent_runtime.authority import RequestAuthority, OperationGrant, bind_request_authority
    authority = RequestAuthority('read-test', 'alice', 's', str(tmp_path), (OperationGrant('read_file'),))
    with bind_request_authority(authority):
        chunks=[c async for c in preview.stream_preview(endpoint_url='http://127.0.0.1:11434/v1/chat/completions',
            model='samantha-mistral:latest',headers={},messages=messages,turn_contract=contract,session_id='s',
            owner='alice',workspace=str(tmp_path),disabled_tools=set(),tool_policy=ToolPolicy(),max_rounds=3)]
    events=[json.loads(c[6:]) for c in chunks if c.startswith('data: {')]
    responses=' '.join(str(e.get('content') or e.get('delta') or '') for e in events if e.get('type')!='tool_output')
    assert 'Ocean' in responses and 'This is a sample PDF.' in responses, [(e.get('type'), e.get('output'), e.get('reason')) for e in events]
    assert 'old failure' not in responses, events
