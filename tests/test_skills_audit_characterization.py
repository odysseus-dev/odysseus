"""Offline contracts captured against the merged skills baseline before extraction."""
import asyncio
from dataclasses import fields, is_dataclass
from difflib import unified_diff
import inspect
import json
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, get_args, get_origin

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from routes import skills_routes as routes


FIXTURES = Path(__file__).parent / 'fixtures'


def _qualified_name(value):
    return f'{value.__module__}.{value.__qualname__}'


def _value_contract(value):
    if value is inspect.Signature.empty:
        return {'empty': True}
    if value is None or type(value) in (bool, int, float, str):
        return {'type': _qualified_name(type(value)), 'value': value}
    if type(value) in (list, tuple):
        return {'type': _qualified_name(type(value)),
                'items': [_value_contract(item) for item in value]}
    if type(value) is dict and all(type(key) is str for key in value):
        return {'type': 'builtins.dict',
                'items': {key: _value_contract(item) for key, item in value.items()}}
    if is_dataclass(value) and not isinstance(value, type):
        return {'type': _qualified_name(type(value)), 'fields': {
            field.name: _value_contract(getattr(value, field.name))
            for field in fields(value)
        }}
    raise TypeError(f'Unsupported contract value: {type(value)}')


def _annotation_contract(annotation):
    if annotation is inspect.Signature.empty or annotation is None:
        return _value_contract(annotation)
    origin = get_origin(annotation)
    if origin is Annotated:
        base, *metadata = get_args(annotation)
        return {'origin': _qualified_name(origin),
                'annotation': _annotation_contract(base),
                'metadata': [_value_contract(item) for item in metadata]}
    if origin is not None:
        return {'origin': _qualified_name(origin), 'module': annotation.__module__,
                'arguments': [_annotation_contract(arg) for arg in get_args(annotation)]}
    return {'type': _qualified_name(annotation)}


def _factory_contract(factory):
    # Names do not identify closure state, partial arguments or arbitrary code.
    # Only these known builtin constructors have a supported default contract.
    if not any(factory is builtin for builtin in (list, tuple, dict, set, frozenset)):
        raise TypeError('Unsupported default factory; requires an explicit compatibility contract')
    return _qualified_name(factory)


def _signature_contract(target):
    """Freeze supported constructor structure without executing default factories."""
    signature = inspect.signature(target)
    model_fields = getattr(target, 'model_fields', {})
    parameters = []
    for parameter in signature.parameters.values():
        field = model_fields.get(parameter.name)
        if field is not None and field.default_factory is not None:
            default = {'factory': _factory_contract(field.default_factory),
                       'marker': _qualified_name(type(parameter.default))}
        else:
            default = _value_contract(parameter.default)
        parameters.append({'name': parameter.name, 'kind': parameter.kind.name,
                           'annotation': _annotation_contract(parameter.annotation),
                           'default': default})
    contract = {'parameters': parameters,
                'return': _annotation_contract(signature.return_annotation)}
    if model_fields:
        # Field names can be absent from the Python signature (validation aliases).
        # These public FieldInfo attributes affect input validation independently
        # of the generated signature and JSON schema. Rebuilt annotations retain
        # field metadata, including constraints and strict/union validation modes.
        contract['model_fields'] = {
            name: {
                'annotation': _annotation_contract(field.rebuild_annotation()),
                'default': ({'factory': _factory_contract(field.default_factory),
                             'takes_validated_data': field.default_factory_takes_validated_data}
                            if field.default_factory is not None else
                            _value_contract(inspect.Signature.empty if field.is_required() else field.default)),
                'validation': {attribute: _value_contract(getattr(field, attribute))
                               for attribute in ('alias', 'alias_priority', 'validation_alias',
                                                 'discriminator', 'validate_default')},
            }
            for name, field in model_fields.items()
        }
        # Includes inherited model settings such as validate_default and strict.
        # Unsupported callables or values fail closed through _value_contract.
        contract['model_config'] = _value_contract(target.model_config)
    return contract


def _canonical_openapi(schema):
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=True) + '\n'


def _assert_openapi_matches_baseline(schema):
    expected = (FIXTURES / 'skills_openapi_baseline.json').read_text(encoding='utf-8')
    actual = _canonical_openapi(schema)
    if actual != expected:
        baseline = json.loads(expected)
        affected = []
        for label, before, after in [
            ('paths', baseline.get('paths', {}), schema.get('paths', {})),
            ('schemas', baseline.get('components', {}).get('schemas', {}),
             schema.get('components', {}).get('schemas', {})),
        ]:
            changed = [key for key in sorted(before.keys() | after.keys())
                       if _canonical_openapi(before.get(key)) != _canonical_openapi(after.get(key))]
            if changed:
                affected.append(f'Changed {label}: {", ".join(changed)}\n')
        pytest.fail('OpenAPI contract changed:\n' + ''.join(affected) + ''.join(unified_diff(
            expected.splitlines(keepends=True), actual.splitlines(keepends=True),
            fromfile='skills_openapi_baseline.json', tofile='generated OpenAPI',
        )))


def endpoint(method, path, manager=None):
    return next(r.endpoint for r in routes.setup_skills_routes(manager).routes
                if method in r.methods and r.path == '/api/skills' + path)


def request(owner='alice', body=None):
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(body or {}).encode()}
    return Request({'type': 'http', 'method': 'POST', 'path': '/',
                    'headers': [(b'content-type', b'application/json')],
                    'state': {'user': owner}}, receive)


def test_api_and_python_signatures_match_baseline():
    baseline = json.loads((FIXTURES / 'skills_api_baseline.json').read_text(encoding='utf-8'))
    app = FastAPI()
    router = routes.setup_skills_routes(object())
    app.include_router(router)
    schema = app.openapi()
    _assert_openapi_matches_baseline(schema)
    declarations = [[sorted(r.methods), r.path, r.name] for r in router.routes]
    assert declarations == baseline['routes']
    for name, signature in baseline['signatures'].items():
        assert _signature_contract(getattr(routes, name)) == signature, name


def test_signature_contract_ignores_annotated_repr(monkeypatch):
    target = routes.SkillImportUrlRequest
    before = _signature_contract(target)
    annotation = inspect.signature(target).parameters['url'].annotation
    monkeypatch.setattr(type(annotation), '__repr__', lambda self: 'different Python rendering')
    assert _signature_contract(target) == before


@pytest.mark.parametrize('change', ['name', 'order', 'kind', 'default', 'annotation', 'return'])
def test_signature_contract_detects_interface_changes(change):
    def original(first: str, second: int = 1) -> bool:
        pass
    signature = inspect.signature(original)
    first, second = signature.parameters.values()
    parameters = [first, second]
    if change == 'order':
        parameters = [second.replace(default=inspect.Parameter.empty),
                      first.replace(default='')]
    elif change == 'return':
        signature = signature.replace(return_annotation=str)
    else:
        changes = {
            'name': {'name': 'renamed'},
            'kind': {'kind': inspect.Parameter.KEYWORD_ONLY},
            'default': {'default': 2},
            'annotation': {'annotation': float},
        }
        parameters[1] = second.replace(**changes[change])
    def changed():
        pass
    changed.__signature__ = signature.replace(parameters=parameters)
    assert _signature_contract(changed) != _signature_contract(original)


@pytest.mark.parametrize('change', ['constraint', 'factory'])
def test_signature_contract_detects_model_changes(change):
    from pydantic import BaseModel, Field

    class Original(BaseModel):
        text: str = Field(max_length=8)
        tags: list[str] = Field(default_factory=list)

    class Changed(BaseModel):
        text: str = Field(max_length=9 if change == 'constraint' else 8)
        tags: list[str] = Field(default_factory=tuple if change == 'factory' else list)

    assert _signature_contract(Changed) != _signature_contract(Original)


@pytest.mark.parametrize('validated_data', [False, True])
def test_signature_contract_rejects_custom_factories_without_calling_them(validated_data):
    from pydantic import BaseModel, Field

    calls = []
    def make_factory(value):
        def plain():
            calls.append(value)
            return [value]
        def with_data(data):
            calls.append(value)
            return [data['prefix'], value]
        return with_data if validated_data else plain

    class Before(BaseModel):
        prefix: str = 'validated'
        tags: list[str] = Field(default_factory=make_factory('before'))

    class After(BaseModel):
        prefix: str = 'validated'
        tags: list[str] = Field(default_factory=make_factory('after'))

    assert Before().tags != After().tags
    assert Before.model_fields['tags'].default_factory_takes_validated_data is validated_data
    calls.clear()
    for model in (Before, After):
        with pytest.raises(TypeError, match='Unsupported default factory'):
            _signature_contract(model)
    assert calls == []


def test_signature_contract_detects_default_validation_outside_signature_and_openapi():
    from pydantic import BaseModel, Field, ValidationError

    class Before(BaseModel):
        count: int = Field(default='invalid', validate_default=False)

    class After(BaseModel):
        count: int = Field(default='invalid', validate_default=True)

    assert Before().count == 'invalid'
    with pytest.raises(ValidationError):
        After()
    assert inspect.signature(Before) == inspect.signature(After)
    before_schema, after_schema = Before.model_json_schema(), After.model_json_schema()
    before_schema.pop('title')
    after_schema.pop('title')
    assert before_schema == after_schema
    assert _signature_contract(Before) != _signature_contract(After)


def test_signature_contract_detects_validation_alias_paths():
    from pydantic import AliasPath, BaseModel, Field, ValidationError

    class Before(BaseModel):
        count: int = Field(validation_alias=AliasPath('before', 0))

    class After(BaseModel):
        count: int = Field(validation_alias=AliasPath('after', 0))

    assert Before(before=[1]).count == 1
    with pytest.raises(ValidationError):
        After(before=[1])
    assert inspect.signature(Before) == inspect.signature(After)
    before_schema, after_schema = Before.model_json_schema(), After.model_json_schema()
    before_schema.pop('title')
    after_schema.pop('title')
    assert before_schema == after_schema
    assert _signature_contract(Before) != _signature_contract(After)


@pytest.mark.parametrize('setting', ['validate_default', 'strict'])
def test_signature_contract_detects_model_validation_configuration(setting):
    from pydantic import BaseModel, ConfigDict, ValidationError

    class Before(BaseModel):
        model_config = ConfigDict({setting: False})
        count: int = 'invalid' if setting == 'validate_default' else 1

    class After(BaseModel):
        model_config = ConfigDict({setting: True})
        count: int = 'invalid' if setting == 'validate_default' else 1

    data = {} if setting == 'validate_default' else {'count': '1'}
    Before(**data)
    with pytest.raises(ValidationError):
        After(**data)
    assert inspect.signature(Before) == inspect.signature(After)
    assert _signature_contract(Before) != _signature_contract(After)


def test_openapi_golden_is_canonical():
    golden = (FIXTURES / 'skills_openapi_baseline.json').read_text(encoding='utf-8')
    schema = json.loads(golden)
    assert golden == _canonical_openapi(schema)


@pytest.mark.parametrize('section, key', [
    ('paths', '/api/skills/add'), ('schemas', 'SkillAddRequest'),
])
def test_openapi_failure_identifies_changed_endpoint_or_schema(section, key):
    schema = json.loads((FIXTURES / 'skills_openapi_baseline.json').read_text(encoding='utf-8'))
    target = schema['paths'] if section == 'paths' else schema['components']['schemas']
    if section == 'paths':
        target[key]['post']['responses']['200']['description'] = 'changed contract'
    else:
        target[key]['properties']['solution']['anyOf'][0]['maxLength'] = 4999
    with pytest.raises(pytest.fail.Exception) as failure:
        _assert_openapi_matches_baseline(schema)
    diagnostic = str(failure.value)
    assert '--- skills_openapi_baseline.json' in diagnostic
    assert '+++ generated OpenAPI' in diagnostic
    assert f'Changed {section}: {key}' in diagnostic
    expected_change = '"description": "changed contract"' if section == 'paths' else '"maxLength": 4999'
    assert any(line.startswith('+') and expected_change in line
               for line in diagnostic.splitlines())


@pytest.mark.parametrize('raw, expected', [
    ('<think>{"verdict":"fail"}</think>{"verdict":"PASS","confidence":2,}', 'pass'),
    ('{"verdict":"fail"} text {"verdict":"needs_work"}', 'needs_work'),
    ('Verdict: INCONCLUSIVE', 'inconclusive'),
    ('<think>{"verdict":"pass"}', 'unknown'),
    ('{"verdict":"invalid"}', 'unknown'),
])
async def test_run_verdict_parsing_and_retry(monkeypatch, raw, expected):
    calls = []
    async def call(url, model, messages, **kwargs):
        calls.append((messages, kwargs))
        return raw
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    monkeypatch.setattr(routes, 'effort_for_call', lambda url, model, owner: 'high' if owner == 'alice' else None)
    result = await routes._eval_skill_run('md', 'task', 'transcript', 'url', 'm', {},
                                          workload='background', owner='alice')
    assert result['verdict'] == expected
    assert len(calls) == (2 if expected == 'unknown' else 1)
    for _, kwargs in calls:
        assert kwargs['reasoning_effort'] == 'high'
        assert kwargs['workload'] == 'background'
        assert kwargs['timeout'] == 180
        assert kwargs['max_tokens'] == 32768
    if len(calls) == 2:
        assert "must START with '{'" in calls[1][0][0]['content']


async def test_review_transient_failure_and_result_coercion(monkeypatch):
    calls = []
    async def call(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError('offline')
        return json.dumps({'verdict': 'pass', 'confidence': '-1', 'summary': 'x' * 500,
                           'issues': ['y' * 250] * 10, 'baseline_verdict': 'invalid',
                           'usefulness': '2', 'saved_turns': '-2', 'saved_tool_calls': 'bad'})
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    result = await routes._eval_skill_run('', '', '', 'url', 'm', None)
    assert result == {'verdict': 'pass', 'confidence': 0.0, 'summary': 'x' * 400,
                      'issues': ['y' * 200] * 8, 'baseline_verdict': 'unknown',
                      'usefulness': 1.0, 'saved_turns': -2, 'saved_tool_calls': 0}


async def test_review_failure_schema_and_transcript_tail(monkeypatch):
    seen = []
    async def call(url, model, messages, **kwargs):
        seen.append(messages)
        raise RuntimeError('offline')
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    result = await routes._eval_skill_run('m' * 5000, 'TASK', 'HEAD' + 'x' * 30000 + 'TAIL', 'url', 'm', None)
    assert result == {'verdict': 'unknown', 'confidence': 0,
                      'summary': 'Review call failed: offline', 'issues': []}
    assert len(seen) == 2
    assert 'HEAD' in seen[0][1]['content'] and 'TAIL' in seen[0][1]['content']
    assert 'm' * 4001 not in seen[0][1]['content']


@pytest.mark.parametrize('name, raw, expected, tokens, timeout', [
    ('_eval_skill_necessity', '<think>x</think>{"necessary":false,"redundant_with":["demo"],"reason":"duplicate",}',
     {'necessary': False, 'redundant_with': ['demo'], 'reason': 'duplicate'}, 8192, 120),
    ('_eval_skill_retrieval_precision', '{"ok":false,"summary":"broad","issues":["metadata: retrieval: narrow"]}',
     {'ok': False, 'summary': 'broad', 'issues': ['metadata: retrieval: narrow']}, 4096, 90),
    ('_eval_skill_necessity', '<think>unfinished', None, 8192, 120),
    ('_eval_skill_retrieval_precision', '[]', None, 4096, 90),
])
async def test_advisory_schema_budget_and_owner(monkeypatch, name, raw, expected, tokens, timeout):
    async def call(*args, **kwargs):
        assert kwargs['max_tokens'] == tokens
        assert kwargs['timeout'] == timeout
        assert kwargs['reasoning_effort'] == 'medium'
        return raw
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    monkeypatch.setattr(routes, 'effort_for_call', lambda url, model, owner: 'medium' if owner == 'alice' else None)
    assert await getattr(routes, name)('md', [], 'url', 'm', None, owner='alice') == expected


@pytest.mark.parametrize('name', ['_eval_skill_necessity', '_eval_skill_retrieval_precision', '_improve_skill_md'])
async def test_advisory_exceptions_return_none(monkeypatch, name):
    async def call(*args, **kwargs):
        raise TimeoutError('offline')
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    args = ('md', {}, '', 'url', 'm', None) if name == '_improve_skill_md' else ('md', [], 'url', 'm', None)
    assert await getattr(routes, name)(*args) is None


async def test_cancelled_audit_closes_stream_in_originating_context(monkeypatch):
    bound = ContextVar('audit_characterization', default=None)
    opened = asyncio.Event()
    closed = []
    async def stream(*args, **kwargs):
        token = bound.set('bound')
        try:
            yield 'data: {"delta":"started"}\n\n'
            opened.set()
            await asyncio.Event().wait()
        finally:
            bound.reset(token)
            closed.append(asyncio.current_task())
    monkeypatch.setattr('src.agent_loop.stream_agent_loop', stream)
    task = asyncio.create_task(routes._run_skill_audit_arm([], 'url', 'm', {}, 'alice'))
    await opened.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [task]
    assert bound.get() is None


async def test_cancelled_batch_skips_finalization_and_cleans_task(monkeypatch):
    opened = asyncio.Event()
    async def audit(*args, **kwargs):
        opened.set()
        await asyncio.Event().wait()
    class Manager:
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': 'demo'}]
    job = {'status': 'running', 'log': [], 'results': [], 'done': 0}
    monkeypatch.setattr(routes, '_skill_audit_jobs', {('alice',): job})
    monkeypatch.setattr(routes, '_audit_one_skill', audit)
    monkeypatch.setattr(routes, '_finalize_audit_batch', lambda *args: pytest.fail('cancelled batch finalized'))
    task = asyncio.create_task(routes._run_audit_all_job(('alice',), Manager(), ['demo'], 'url', 'm', {}, None, 'alice'))
    job['task'] = task
    await opened.wait()
    task.cancel()
    await task
    assert job['status'] == 'cancelled'
    assert job['current'] is None and 'task' not in job and 'finished' in job
    assert job['results'] == [] and job['done'] == 0


async def test_test_polling_is_owner_scoped_and_hides_continuation(monkeypatch):
    class Manager:
        def load(self, owner=None):
            return [{'name': 'demo'}]
    monkeypatch.setattr(routes, 'get_current_user', lambda req: req.state.user)
    job = {'status': 'awaiting_approval', 'task': 'task', 'model': 'm', 'log': [],
           'verdict': None, 'approval': {'approval_id': 'id'},
           '_run': {'headers': {'Authorization': 'secret'}}, '_transcript': ['private']}
    monkeypatch.setattr(routes, '_skill_test_jobs', {('alice', 'demo'): job})
    poll = endpoint('GET', '/{skill_id}/test-status', Manager())
    assert await poll(request('bob'), 'demo') == {'status': 'none'}
    assert await poll(request(), 'demo') == {k: job[k] for k in ('status', 'task', 'model', 'log', 'verdict', 'approval')}


def test_batch_finalizer_protects_approval_required_and_unprocessed_skills(monkeypatch):
    class Manager:
        def __init__(self):
            self.updated = []
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': name, 'status': 'published', 'source': 'learned',
                     'audit_verdict': 'fail', 'confidence': .9} for name in ('demo', 'demo-1', 'demo-2')]
        def update_skill(self, name, fields, owner=None):
            self.updated.append((name, fields, owner))
    monkeypatch.setattr(routes, '_audit_auto_publish_policy', lambda owner: (True, .8))
    manager = Manager()
    routes._finalize_audit_batch(manager, [{'skill': 'demo', 'result': 'approval_required'},
                                          {'skill': 'demo-1', 'result': 'flagged'}], 'alice', lambda msg: None)
    assert manager.updated == [('demo-1', {'status': 'draft'}, 'alice')]
