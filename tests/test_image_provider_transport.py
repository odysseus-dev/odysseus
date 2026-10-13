import asyncio
import base64
import json

import httpx
import pytest
from PIL import Image

from src import ai_interaction, database, settings


def test_native_generation_uses_configured_model_and_multiline_prompt(image_transport, monkeypatch):
    configure, source, requests, rows = image_transport
    configure('https://openrouter.ai/api/v1')
    monkeypatch.setattr(settings, 'load_settings', lambda: {'image_model': 'openai/gpt-5-image'})
    seen = []
    def resolve(model, **kwargs):
        seen.append((model, kwargs.get('owner')))
        return 'https://openrouter.ai/api/v1/chat/completions', model, {}
    monkeypatch.setattr(ai_interaction, '_resolve_model', resolve)
    prompt = 'A thumbnail\nwith the title: My AI'
    result = asyncio.run(ai_interaction.do_generate_image(
        json.dumps({'prompt': prompt}), session_id='fixture', owner='alice'))
    assert 'error' not in result
    assert seen == [('openai/gpt-5-image', 'alice')]
    assert json.loads(requests[0].content)['prompt'] == prompt
    assert result['image_id']


def test_native_generation_respects_admin_disable(monkeypatch):
    monkeypatch.setattr(settings, 'load_settings', lambda: {'image_gen_enabled': False})
    result = asyncio.run(ai_interaction.do_generate_image(json.dumps({'prompt': 'A city'})))
    assert 'disabled' in result['error']


@pytest.fixture
def image_transport(monkeypatch, tmp_path):
    rows = []

    class GalleryDb:
        def add(self, row):
            rows.append(row)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, 'SessionLocal', GalleryDb)
    monkeypatch.setattr(settings, 'load_settings', lambda: {})
    monkeypatch.setattr(ai_interaction, 'GENERATED_IMAGES_DIR', str(tmp_path / 'generated'))
    source = tmp_path / 'input.png'
    Image.new('RGB', (120, 80), 'red').save(source)
    original_client = httpx.AsyncClient
    requests = []

    def configure(base, status=200, body=None):
        monkeypatch.setattr(ai_interaction, '_resolve_model', lambda *args, **kwargs: (
            base + '/chat/completions', 'openai/gpt-5-image', {'Authorization': 'Bearer test'},
        ))

        def handle(request):
            requests.append(request)
            return httpx.Response(status, json=body if body is not None else {
                'data': [{'b64_json': base64.b64encode(source.read_bytes()).decode()}],
            })

        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
            transport=httpx.MockTransport(handle), **kwargs,
        ))

    return configure, source, requests, rows


@pytest.mark.parametrize('editing', [True, False])
@pytest.mark.parametrize('base', ['https://openrouter.ai/api/v1', 'https://api.openai.com/v1'])
def test_image_provider_protocol(image_transport, editing, base):
    configure, source, requests, rows = image_transport
    configure(base)

    async def progress(_data):
        pass

    if editing:
        result = asyncio.run(ai_interaction.do_edit_image(
            'Change the background', str(source), model_spec='openai/gpt-5-image',
            owner='alice', session_id='session-1', progress_callback=progress,
        ))
    else:
        result = asyncio.run(ai_interaction.do_generate_image(
            'A thumbnail\nopenai/gpt-5-image', owner='alice', session_id='session-1',
        ))
    assert 'error' not in result
    posts = [r for r in requests if r.method == 'POST']
    assert len(posts) == 1
    request = posts[0]
    assert request.headers['authorization'] == 'Bearer test'
    if 'openrouter.ai' in base:
        assert len(requests) == 1  # No unsupported local progress/fallback probes.
        assert str(request.url) == base + '/images'
        payload = json.loads(request.content)
        assert payload['n'] == 1
        if editing:
            assert payload['size'] == '1536x1024'
            reference = payload['input_references'][0]['image_url']['url']
            assert reference.startswith('data:image/png;base64,')
            assert base64.b64decode(reference.split(',', 1)[1]) == source.read_bytes()
            assert 'request_id' not in payload
            assert 'response_format' not in payload
    else:
        assert str(request.url) == base + ('/images/edits' if editing else '/images/generations')
        if editing:
            assert 'multipart/form-data' in request.headers['content-type']
            assert source.read_bytes() in request.content
    assert len(rows) == 1
    assert rows[0].owner == 'alice'
    assert rows[0].session_id == 'session-1'
    assert (source.parent / 'generated' / rows[0].filename).read_bytes() == source.read_bytes()
    if editing:
        assert result['image_size'] == rows[0].size == '120x80'


@pytest.mark.parametrize('status', [400, 401, 404, 422, 500])
def test_openrouter_edit_reports_provider_error_without_local_fallback(image_transport, status):
    configure, source, requests, rows = image_transport
    configure('https://openrouter.ai/api/v1', status, {'error': {'message': 'Provider rejected edit'}})
    result = asyncio.run(ai_interaction.do_edit_image('Edit', str(source), model_spec='openai/gpt-5-image'))
    assert f'({status}): Provider rejected edit' in result['error']
    assert len(requests) == 1
    assert not rows


@pytest.mark.parametrize('dimensions,expected', [
    ((1920, 1080), '1536x1024'),
    ((1080, 1920), '1024x1536'),
    ((512, 512), '1024x1024'),
    ((1000, 1100), '1024x1024'),
])
def test_edit_size_matches_closest_supported_shape(dimensions, expected):
    from src.image_model_ids import image_edit_size
    assert image_edit_size('openai/gpt-5-image', *dimensions) == expected
    assert image_edit_size('local-edit', *dimensions) == f'{dimensions[0]}x{dimensions[1]}'


@pytest.mark.parametrize('explicit', [False, True])
def test_edit_dimensions_respect_exif_and_explicit_size(image_transport, explicit):
    configure, source, requests, rows = image_transport
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (120, 80)).save(source, exif=exif)
    configure('https://openrouter.ai/api/v1')
    result = asyncio.run(ai_interaction.do_edit_image(
        'Edit', str(source), model_spec='openai/gpt-5-image',
        size='1024x1024' if explicit else 'auto',
    ))
    assert 'error' not in result
    assert json.loads(requests[0].content)['size'] == ('1024x1024' if explicit else '1024x1536')


def test_unreadable_image_does_not_call_provider(image_transport):
    configure, source, requests, rows = image_transport
    source.write_bytes(b'invalid')
    configure('https://openrouter.ai/api/v1')
    result = asyncio.run(ai_interaction.do_edit_image('Edit', str(source), model_spec='openai/gpt-5-image'))
    assert 'dimensions' in result['error']
    assert not requests
