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
        async def post(self, url, json):
            if json['messages'][-1]['content'].startswith('Tool result (data):'):
                return Response({'name':'__answer__','arguments':{'content':'The PDF is ready.'}})
            return Response(proposal)
    monkeypatch.setattr(preview.httpx, 'AsyncClient', MockClient)
    schemas=[s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name']=='write_file']
    contract=resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy())
    chunks=[c async for c in preview.stream_preview(endpoint_url='http://127.0.0.1:11434/v1/chat/completions',
        model='samantha-mistral:latest',headers={},messages=[{'role':'user','content':'Create a PDF named samantha.pdf with Ocean'}],
        turn_contract=contract,session_id='s',owner='alice',workspace=str(tmp_path),disabled_tools=set(),tool_policy=ToolPolicy(),max_rounds=3)]
    assert (tmp_path/'samantha.pdf').is_file(), chunks
    assert any('[Download PDF]' in c for c in chunks), chunks


@pytest.mark.asyncio
async def test_preview_adapter_continues_to_second_requested_pdf(tmp_path, monkeypatch):
    import src.clean_agent_preview as preview
    from src import ollama_tool_detection, tool_execution, tool_utils
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract
    from src.upload_handler import UploadHandler
    monkeypatch.setattr(ollama_tool_detection, 'native_tool_capability', lambda *args: False)
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    monkeypatch.setattr(tool_utils, '_upload_handler', UploadHandler(str(tmp_path), str(tmp_path/'uploads')))
    proposals = iter([
        {'name':'write_file','arguments':{'path':'a.pdf','content':'First','format':'pdf'}},
        {'name':'write_file','arguments':{'path':'b.pdf','content':'Second','format':'pdf'}},
        {'name':'__answer__','arguments':{'content':'Both PDFs are ready.'}},
    ])
    class MockClient(Client):
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json): return Response(next(proposals))
    monkeypatch.setattr(preview.httpx, 'AsyncClient', MockClient)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'write_file']
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    chunks = [c async for c in preview.stream_preview(endpoint_url='http://127.0.0.1:11434/v1/chat/completions',
        model='test-model', headers={}, messages=[{'role':'user',
            'content':'Create a PDF named a.pdf and another named b.pdf'}],
        turn_contract=contract, session_id='s', owner='alice', workspace=str(tmp_path),
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=4)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: {')]
    assert (tmp_path/'a.pdf').is_file(), events
    second = [e for e in events if e.get('type') == 'tool_output' and 'b.pdf' in e.get('command', '')]
    assert second and (second[0].get('approval_required') or not second[0].get('error')), events


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
async def test_read_and_answer_uses_model_to_interpret_result(tool_request):
    tool_request['messages'] = [
        {'role':'user','content':'Read example.pdf and tell me its title and sentence.'},
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'read_file','arguments':'{"path":"example.pdf"}'}}]},
        {'role':'tool','tool_call_id':'one','content':'[Page 1 text]:\nTitle: Ocean\nThis is a sample PDF.'},
    ]
    client = Client({'name':'__answer__','arguments':{'content':'Ocean. This is a sample PDF.'}})
    result=await generate_proposal(client,tool_request)
    assert client.sent is not None
    assert 'Ocean' in result['content'] and 'This is a sample PDF.' in result['content']


@pytest.mark.asyncio
async def test_read_and_question_does_not_return_raw_file(tool_request):
    tool_request['messages'] = [
        {'role':'user','content':'Read report.txt and tell me whether its contents contain errors.'},
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'read_file','arguments':'{"path":"report.txt"}'}}]},
        {'role':'tool','tool_call_id':'one','content':'raw report contents'},
    ]
    client = Client({'name':'__answer__','arguments':{'content':'No errors found.'}})
    assert await generate_proposal(client, tool_request) == {'content':'No errors found.'}
    assert any('raw report contents' in m['content'] for m in client.sent[1]['messages'])


@pytest.mark.asyncio
async def test_reading_multiple_files_can_continue_after_first(tool_request):
    tool_request['tools'] = [{'type':'function','function':{'name':'read_file',
        'parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path']}}}]
    tool_request['messages'] = [
        {'role':'user','content':'Read a.txt and b.txt and show their contents.'},
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'read_file','arguments':'{"path":"a.txt"}'}}]},
        {'role':'tool','tool_call_id':'one','content':'first file'},
    ]
    result = await generate_proposal(Client({'name':'read_file','arguments':{'path':'b.txt'}}), tool_request)
    assert json.loads(result['tool_calls'][0]['function']['arguments']) == {'path':'b.txt'}


@pytest.mark.asyncio
async def test_images_in_current_and_earlier_turns_reach_ollama(tool_request):
    tool_request['messages'] = [
        {'role':'user','content':[{'type':'text','text':'Earlier image'},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,YWJj'}}]},
        {'role':'assistant','content':'Earlier answer'},
        {'role':'user','content':[{'type':'text','text':'What is in this image?'},
            {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,ZGVm'}}]},
    ]
    client = Client({'name':'__answer__','arguments':{'content':'A picture.'}})
    await generate_proposal(client, tool_request)
    messages = client.sent[1]['messages']
    assert [(m['content'], m.get('images')) for m in messages if m.get('images')] == [
        ('Earlier image', ['YWJj']), ('What is in this image?', ['ZGVm'])]


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
        async def post(self, url, json):
            if json['messages'][-1]['content'].startswith('Tool result (data):'):
                return Response({'name':'__answer__','arguments':{'content':'Title: Ocean. This is a sample PDF.'}})
            return Response(proposal)
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
