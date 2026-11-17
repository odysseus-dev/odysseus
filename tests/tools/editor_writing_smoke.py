"""Opt-in Ajax smoke test: temporary SQLite docs, stubbed web, no real mutations.

Run from repository root with PYTHONPATH=. and ODYSSEUS_EDITOR_TEST_ENDPOINT set.
Optional ODYSSEUS_EDITOR_ACTIONS selects comma-separated actions.
"""
import asyncio, json, os, re, tempfile
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core import database
from src import database as compat
from src.agent_tools import TOOL_HANDLERS
import src.clean_agent_preview as runner
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from tests.helpers.document_source import declaration
from src.turn_contract import requested_capabilities, selected_tools_for_request, preserve_bound_editor_selected_tools, resolve_turn_contract
menu=declaration('_AI_WRITING_ACTIONS')
actions=dict(re.findall(r"\s+(\w+): '([^']+)'",menu))
image_body = '<p>Old caption</p><p><img src="/api/generated-image/fixture.png" alt="Fixture image" width="320" height="180"></p><p>Keep this paragraph unchanged.</p>'
email_body = ('To: sam@example.com\nSubject: Re: Design review\nIn-Reply-To: <fixture@example.com>\n---\n'
              '<div class="email-reply-edit-slot"><p>Hi Sam,</p><p>I can attend on Tuesday.</p><p>Thanks,<br>Alex</p></div>'
              '<div class="email-quoted-history" contenteditable="false"><p>Sam wrote: Can you attend on Tuesday?</p></div>')
edit_cases = {
 'rich-image-caption': ('richtext', image_body,
                        'Change Old caption to New caption in the open document. Preserve the embedded image, its attributes, and all other text.',
                        lambda value: value == image_body.replace('Old caption', 'New caption')),
 'email-reply-only': ('email', email_body,
                      'Change Tuesday to Wednesday only in my reply, not in the quoted email. Keep the recipient, subject, signature and quoted history unchanged.',
                      lambda value: value == email_body.replace('I can attend on Tuesday.', 'I can attend on Wednesday.')),
 'email-subject-only': ('email', email_body,
                        'Change only the subject of this open email draft to Schedule confirmation. Leave all other headers, reply text and quoted history unchanged.',
                        lambda value: value == email_body.replace('Subject: Re: Design review', 'Subject: Schedule confirmation')),
 'email-recipient-only': ('email', email_body,
                          'Change only the recipient of this open email draft to jordan@example.com. Do not send it. Keep the greeting, subject, body and quoted history unchanged.',
                          lambda value: value == email_body.replace('To: sam@example.com', 'To: jordan@example.com')),
 'email-signature-only': ('email', email_body,
                          'Change Alex to Alexandra only in my reply signature. Keep all headers, other reply text, formatting and quoted history unchanged.',
                          lambda value: value == email_body.replace('Thanks,<br>Alex', 'Thanks,<br>Alexandra')),
 'delete-line': ('text', 'Alpha\nRemove this line\nGamma\n',
                 'Delete only the line Remove this line, including its newline. Keep everything else unchanged.',
                 lambda value: value == 'Alpha\nGamma\n'),
 'replace-all': ('text', 'alpha is first.\nalpha is repeated.\nKeep: violet-72\n',
                 'Replace every occurrence of alpha with beta. Keep everything else unchanged.',
                 lambda value: value == 'beta is first.\nbeta is repeated.\nKeep: violet-72\n'),
 'append-line': ('text', 'Alpha\nBeta\n',
                 'Append Gamma on a new final line, ending with a newline. Keep the existing two lines unchanged and do not add a blank line.',
                 lambda value: value == 'Alpha\nBeta\nGamma\n'),
 'markdown-table': ('markdown', '| Item | Status |\n| --- | --- |\n| Alpha | pending |\n| Beta | pending |\n\nKeep this paragraph unchanged.',
                    'Change only the Beta row status from pending to done. Keep the Alpha row and all other text unchanged.',
                    lambda value: value == '| Item | Status |\n| --- | --- |\n| Alpha | pending |\n| Beta | done |\n\nKeep this paragraph unchanged.'),
 'rich-link-text': ('richtext', '<p>Read <a href="https://example.com/manual" target="_blank">old guide</a> before starting.</p><p><strong>Keep this paragraph unchanged.</strong></p>',
                    'Change the link text from old guide to setup guide. Keep its URL, attributes, formatting and all other text unchanged.',
                    lambda value: value == '<p>Read <a href="https://example.com/manual" target="_blank">setup guide</a> before starting.</p><p><strong>Keep this paragraph unchanged.</strong></p>'),
 'json-value': ('json', '{"name":"outer","settings":{"name":"inner","enabled":true},"count":7}',
                'Change settings.name to updated. Leave the outer name and all other values unchanged.',
                lambda value: json.loads(value) == {'name':'outer','settings':{'name':'updated','enabled':True},'count':7}),
 'repeated': ('text', 'First: alpha\nSecond: alpha\nKeep: violet-72',
              'Change only the second alpha to beta. Keep everything else unchanged.',
              lambda value: value == 'First: alpha\nSecond: beta\nKeep: violet-72'),
 'quoted-code': ('javascript', 'const label = "old";\nconst count = 7;\n',
                 'Change the label string to new. Keep the count and all other code unchanged.',
                 lambda value: value == 'const label = "new";\nconst count = 7;\n'),
 'svg-color': ('svg', '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40" fill="orange"/></svg>',
               'Make the circle white without changing its size or position.',
               lambda value: value == '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40" fill="white"/></svg>'),
}
async def main():
 failures=[]
 selected_actions = os.environ.get('ODYSSEUS_EDITOR_ACTIONS', ','.join([*actions,'edit','update'])).split(',')
 unknown = set(selected_actions) - (set(actions) | set(edit_cases) | {'edit', 'update'})
 if unknown: raise ValueError('Unknown editor test actions: ' + ', '.join(sorted(unknown)))
 with tempfile.TemporaryDirectory(prefix='editor-fixture-') as tmp:
  engine=create_engine('sqlite:///'+tmp+'/test.db');database.Base.metadata.create_all(engine)
  factory=sessionmaker(bind=engine);database.SessionLocal=compat.SessionLocal=factory
  for action in selected_actions:
   body='The calendar has events and notes about Python scripts. This sentnce is unecessarily long and it is very very unclear.\n\nKeep this paragraph unchanged.'
   if action=='sources': body='Water freezes at 10 degrees Celsius at standard pressure.\n\nKeep this paragraph unchanged.'
   rich_fixture = os.environ.get('ODYSSEUS_EDITOR_RICH_FIXTURE') == '1'
   if rich_fixture and action != 'sources':
    body = ''.join(f'<p>Section {i}: This sentnce has an unecessary delay. That banana is ripe; being patient helps.</p>' for i in range(1, 9)) + '<p><strong>Keep this paragraph unchanged.</strong></p>'
   language = 'richtext' if rich_fixture else 'markdown'
   if action in edit_cases:
    language, body, _, _ = edit_cases[action]
   with factory() as db:
    db.add(database.Document(id=action,owner='fixture-owner',title='Fixture',language=language,current_content=body));db.commit()
   doc=SimpleNamespace(id=action,title='Fixture',language=language,current_content=body)
   prompt=actions.get(action, 'Fix sentnce to sentence in the open document.' if action=='edit' else 'Rewrite the whole open document as two short bullet points.')
   if action in edit_cases: prompt = edit_cases[action][2]
   if action in actions:
    if action=='style': prompt+='\n\nUse this configured writing style as the source of truth:\n---\nUse plain, concise English.\n---'
    prompt+='\n\nImportant scope: work only on this selected passage and do not suggest changes elsewhere in the document. Selected passage:\n---\n'+body.split('\n\n')[0]+'\n---'
   if rich_fixture:
    prompt = prompt.split('\n\nImportant scope:')[0] + '\n\nThere is no selection. Work on the whole open document.'
   calls=[]
   async def execute(block, **kwargs):
    name=block.tool_type
    calls.append(name)
    if name in {'web_search','web_fetch','private_browser'}:
     return 'fixture evidence',{'output':json.dumps({'results':[{'title':'Freezing point of water','url':'https://example.com/water','content':'At standard pressure, water freezes at 0 degrees Celsius.'}]}),'exit_code':0}
    assert name in {'suggest_document','edit_document','update_document'},name
    result=await TOOL_HANDLERS[name](block.content,{'owner':'fixture-owner','doc_id':action})
    return name,result
   runner.execute_tool_block=execute
   policy=ToolPolicy();families=requested_capabilities(prompt,active_document=True)
   selected=preserve_bound_editor_selected_tools(prompt,selected_tools_for_request(prompt),active_document=True)
   contract=resolve_turn_contract(capabilities=families,schemas=FUNCTION_TOOL_SCHEMAS,policy=policy,selected_tools=selected)
   events=[]
   async for chunk in runner.stream_preview(endpoint_url=os.environ['ODYSSEUS_EDITOR_TEST_ENDPOINT'],model='Ajax',headers={},turn_contract=contract,messages=[{'role':'user','content':prompt}],session_id='fixture-editor',owner='fixture-owner',disabled_tools=set(),tool_policy=policy,thinking_mode='off',active_document=doc,max_tokens=int(os.environ.get("ODYSSEUS_EDITOR_MAX_TOKENS", "4096"))):
    if chunk.startswith('data: ') and '[DONE]' not in chunk:
     event=json.loads(chunk[6:]); events.append(event)
     if event.get('type') == 'tool_output': print('CHECK', action, event.get('tool'), 'round=' + str(event.get('round')), 'error=' + str(event.get('error')), flush=True)
   with factory() as db: after=db.get(database.Document,action).current_content
   suggestions=[e for e in events if e.get('type')=='doc_suggestions']
   outputs=[{'tool':e['tool'],'error':e.get('error'),'output':e.get('output','')[:200], 'arguments':e.get('command','')[:2000]} for e in events if e.get('type')=='tool_output']
   ok=bool(suggestions) and after==body if action in actions and action!='proofread' else after!=body
   if action=='sources':ok=ok and any(n in calls for n in ['web_search','web_fetch','private_browser'])
   if action in actions and action!='proofread':
    ok=ok and all(item['find'] in body.split('\n\n')[0] for event in suggestions for item in event['suggestions'])
    if action=='concise':
     from src.clean_agent_preview import document_suggestion_quality_error
     ok=ok and all(document_suggestion_quality_error('suggest_document',
      {'suggestions': event['suggestions']}, user_text=prompt) is None for event in suggestions)
   if action in {'edit','proofread'}: ok=ok and 'Keep this paragraph unchanged.' in after
   if action == 'proofread': ok = ok and 'sentnce' not in after and 'unecessarily' not in after
   if action in edit_cases: ok = edit_cases[action][3](after)
   if rich_fixture and action=='proofread':
    ok = ok and 'sentnce' not in after and 'unecessary' not in after
    ok = ok and after.count('That banana is ripe; being patient helps.') == 8
    ok = ok and after.count('<p>') == 9 and '<strong>Keep this paragraph unchanged.</strong>' in after
   if not ok: failures.append(action)
   answer = ''
   for event in events:
    if event.get('type') == 'final_response': answer = event.get('content') or ''
    elif isinstance(event.get('delta'), str):
     if event.get('replacement_scope') == 'turn': answer = ''
     answer += event['delta']
   print(json.dumps({'action':action,'pass':ok,'failed_attempts':sum(bool(o.get('error')) for o in outputs),'calls':calls,'suggestions':suggestions,'outputs':outputs,'changed':after!=body,'answer':answer}),flush=True)
   if os.environ.get('ODYSSEUS_EDITOR_TRACE'):
    print('EVENTS', json.dumps(events), flush=True)
  engine.dispose()
 if failures: raise SystemExit('Failed writing actions: '+', '.join(failures))
if __name__ == '__main__':
 asyncio.run(main())
