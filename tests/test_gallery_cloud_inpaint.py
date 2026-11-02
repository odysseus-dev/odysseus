import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import routes.gallery_routes as gallery


def png(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize('model', ['openai/gpt-5-image', 'openai/gpt-image-2'])
@pytest.mark.parametrize('saved_settings', [False, True])
@pytest.mark.parametrize('full_frame', [False, True])
def test_openrouter_inpaint_keeps_unmasked_pixels(monkeypatch, caplog, model, saved_settings, full_frame):
    import logging
    caplog.set_level(logging.DEBUG)
    endpoint = SimpleNamespace(base_url='https://openrouter.ai/api/v1', api_key='test')

    class Db:
        def close(self):
            pass

    monkeypatch.setattr(gallery, 'SessionLocal', Db)
    monkeypatch.setattr(gallery, 'require_privilege', lambda *args: 'alice')
    monkeypatch.setattr(gallery, '_visible_image_endpoint_for_base', lambda *args: endpoint)
    monkeypatch.setattr('src.url_safety.check_outbound_url', lambda *args, **kwargs: (True, ''))
    monkeypatch.setattr('src.editor_image_settings.load_settings', lambda: {'image_model':'wrong-generation-model', 'image_edit_model':model})
    def resolve_model(selected, **kwargs):
        assert selected == model
        assert kwargs['owner'] == 'alice'
        return endpoint.base_url + '/chat/completions', model, {}
    monkeypatch.setattr('src.ai_interaction._resolve_model', resolve_model)
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == endpoint.base_url + ('/chat/completions' if model == 'openai/gpt-5-image' else '/images')
        payload = json.loads(request.content)
        assert payload['model'] == model
        if model == 'openai/gpt-5-image':
            assert len(payload['messages'][0]['content']) == 3
        else:
            assert len(payload['input_references']) == 2
        assert request.headers['authorization'] == 'Bearer test'
        image = png(Image.new('RGB', (4, 4) if full_frame else (2, 1), 'blue'))
        if model == 'openai/gpt-5-image':
            return httpx.Response(200, json={'choices':[{'message':{'images':[{'image_url':{'url':'data:image/png;base64,'+image}}]}}]})
        return httpx.Response(200, json={'data': [{'b64_json': image}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    mask = Image.new('L', (2, 1))
    if full_frame:
        mask.paste(255, (0,0,2,1))
    mask.putpixel((1, 0), 255)

    class Request:
        async def json(self):
            return {'_endpoint': endpoint.base_url, '_model': 'stale-model' if saved_settings else model,
                    '_use_image_settings':saved_settings,
                    'image': png(Image.new('RGB', (2, 1), 'red')), 'mask': png(mask), 'prompt': 'private-cloud-prompt'}

    route = next(r for r in gallery.setup_gallery_routes().routes if r.path == '/api/image/inpaint')
    result = asyncio.run(route.endpoint(Request()))
    output = Image.open(io.BytesIO(base64.b64decode(result['image'])))
    if full_frame:
        assert output.size == (4,4)
        assert result['full_frame'] is True
    else:
        assert output.getpixel((0, 0)) == (255, 0, 0, 255)
        assert output.getpixel((1, 0)) == (0, 0, 255, 255)
    assert len(requests) == 1
    assert 'private-cloud-prompt' not in caplog.text


def test_partial_image_edit_preserves_ratio_and_unmasked_pixels():
    source = Image.new('RGBA',(4,4),'red')
    generated = Image.new('RGBA',(4,2),'blue')
    mask = Image.new('L',(4,4),255)
    mask.putpixel((0,0),0)
    result, full = gallery._compose_cloud_image_edit(source,generated,mask)
    assert full is False
    assert result.size == (4,4)
    assert result.getpixel((2,0)) == source.getpixel((2,0))
    assert result.getpixel((2,3)) == source.getpixel((2,3))
    assert result.getpixel((2,1)) == (0,0,255,255)


def test_quick_edit_inpaint_requests_source_ratio_without_width_height_fields(monkeypatch):
    endpoint = SimpleNamespace(base_url='https://openrouter.ai/api/v1',api_key='test')
    monkeypatch.setattr(gallery,'SessionLocal',lambda:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(gallery,'require_privilege',lambda *args:'alice')
    monkeypatch.setattr(gallery,'_visible_image_endpoint_for_base',lambda *args:endpoint)
    monkeypatch.setattr('src.url_safety.check_outbound_url',lambda *args,**kwargs:(True,''))
    sent = []
    original = httpx.AsyncClient

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200,json={'data':[{'b64_json':png(Image.new('RGB',(1536,864),'blue'))}]})

    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))

    class Request:
        async def json(self):
            return {'_endpoint':endpoint.base_url,'_model':'openai/gpt-image-2','prompt':'change the sky',
                    'image':png(Image.new('RGB',(1280,720),'red')),'mask':png(Image.new('L',(1280,720),255))}

    route = next(route for route in gallery.setup_gallery_routes().routes if route.path == '/api/image/inpaint')
    result = asyncio.run(route.endpoint(Request()))
    assert sent[0]['aspect_ratio'] == '16:9'
    assert 'size' not in sent[0]
    image = Image.open(io.BytesIO(base64.b64decode(result['image'])))
    assert image.size == (1536,864)


def test_mixed_endpoint_requires_configured_image_model(monkeypatch):
    endpoint = SimpleNamespace(base_url='https://openrouter.ai/api/v1', owner='alice',
                               model_type='llm', cached_models='["openai/gpt-5-image", "text-model"]', pinned_models=None)

    class Query:
        def query(self, *args):
            return self

        def filter(self, *args):
            return self

        def all(self):
            return [endpoint]

    monkeypatch.setattr('src.auth_helpers.owner_filter', lambda query, model, owner: query)
    lookup = gallery._visible_image_endpoint_for_base
    assert lookup(Query(), endpoint.base_url, 'alice', 'openai/gpt-5-image') is endpoint
    assert lookup(Query(), endpoint.base_url, 'alice', 'text-model') is None
    assert lookup(Query(), endpoint.base_url, 'alice', 'openai/gpt-image-1') is None
    assert lookup(Query(), 'https://unregistered.test/v1', 'alice', 'openai/gpt-5-image') is None


def _form_fields(content, content_type):
    """Parse simple multipart form fields (text parts only) from a request body."""
    boundary = content_type.split('boundary=')[1].strip().strip('"')
    fields = {}
    for part in content.split(('--' + boundary).encode()):
        if b'name="' not in part:
            continue
        head, _, body = part.partition(b'\r\n\r\n')
        import re as _re
        match = _re.search(rb'name="([^"]+)"', head)
        if not match:
            continue
        fields[match.group(1).decode()] = body.rstrip(b'\r\n-').decode('latin1')
    return fields


def _self_hosted_inpaint(monkeypatch, image, mask, body_extra, respond, include_mask=True):
    base_url = 'http://100.69.120.65:8188/v1'
    endpoint = SimpleNamespace(base_url=base_url, api_key='local', owner='alice')
    monkeypatch.setattr(gallery, 'SessionLocal', lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(gallery, 'require_privilege', lambda *a: 'alice')
    monkeypatch.setattr(gallery, '_visible_image_endpoint_for_base', lambda *a: endpoint)
    monkeypatch.setattr('src.url_safety.check_outbound_url', lambda *a, **k: (True, ''))
    _real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient',
                        lambda **kw: _real_client(transport=httpx.MockTransport(respond), **kw))

    class Request:
        async def json(self):
            payload = {'_endpoint': base_url, 'image': png(image), 'prompt': 'do a thing'}
            if include_mask:
                payload['mask'] = png(mask)
            payload.update(body_extra)
            return payload

    route = next(r for r in gallery.setup_gallery_routes().routes if r.path == '/api/image/inpaint')
    return asyncio.run(route.endpoint(Request()))


def test_self_hosted_inpaint_caps_oversize_and_restores_dimensions(monkeypatch):
    seen = {}

    def respond(request):
        assert str(request.url) == 'http://100.69.120.65:8188/v1/images/edits'
        seen.update(_form_fields(request.content, request.headers['content-type']))
        return httpx.Response(200, json={'data': [{'b64_json': png(Image.new('RGB', (768, 576), 'blue'))}]})

    out = _self_hosted_inpaint(monkeypatch, Image.new('RGB', (1600, 1200), 'red'),
                               Image.new('L', (1600, 1200), 255),
                               {'steps': 8}, respond)
    # Working size is capped to a model-friendly size (<=768 long side, /16).
    assert seen['size'] == '768x576'
    # A requested step budget passes through up to the cap (default 8).
    assert seen['steps'] == '8'
    # The returned image keeps the original dimensions.
    assert Image.open(io.BytesIO(base64.b64decode(out['image']))).size == (1600, 1200)


def test_self_hosted_inpaint_clamps_steps_to_the_cap(monkeypatch):
    seen = {}

    def respond(request):
        seen.update(_form_fields(request.content, request.headers['content-type']))
        return httpx.Response(200, json={'data': [{'b64_json': png(Image.new('RGB', (640, 480), 'blue'))}]})

    _self_hosted_inpaint(monkeypatch, Image.new('RGB', (640, 480), 'red'),
                         Image.new('L', (640, 480), 255), {'steps': 20}, respond)
    # Over-cap requests are clamped to the configured maximum (default 8).
    assert seen['steps'] == '8'


def test_self_hosted_inpaint_leaves_small_images_unchanged(monkeypatch):
    seen = {}

    def respond(request):
        seen.update(_form_fields(request.content, request.headers['content-type']))
        return httpx.Response(200, json={'data': [{'b64_json': png(Image.new('RGB', (640, 480), 'green'))}]})

    out = _self_hosted_inpaint(monkeypatch, Image.new('RGB', (640, 480), 'red'),
                               Image.new('L', (640, 480), 255), {}, respond)
    # Under the cap: dimensions pass through untouched; with no requested count
    # sent the moderate default (4) is used.
    assert seen['size'] == '640x480'
    assert seen['steps'] == '4'
    assert Image.open(io.BytesIO(base64.b64decode(out['image']))).size == (640, 480)


def test_inpaint_does_not_log_private_prompt_at_debug_level(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    secret = 'private-prompt-log-regression'

    def respond(request):
        return httpx.Response(200, json={'data': [{'b64_json': png(Image.new('RGB', (4, 4), 'blue'))}]})

    _self_hosted_inpaint(monkeypatch, Image.new('RGB', (4, 4), 'red'),
                         Image.new('L', (4, 4), 255), {'prompt': secret}, respond)
    assert secret not in caplog.text


def test_self_hosted_inpaint_without_mask_uses_a_full_white_mask(monkeypatch):
    """A whole-image instruction edit may omit the mask; it must not raise
    KeyError -> 400 (the health-audit regression)."""
    seen = {}
    mask_bytes = {}

    def respond(request):
        seen.update(_form_fields(request.content, request.headers['content-type']))
        # The provider must receive a (full-white) mask, not a missing one.
        import re as _re
        parts = request.content.split(b'--')
        for part in parts:
            if b'name="mask"' in part:
                head, _, body = part.partition(b'\r\n\r\n')
                mask_bytes['png'] = body.rstrip(b'\r\n-')
        return httpx.Response(200, json={'data': [{'b64_json': png(Image.new('RGB', (32, 32), 'blue'))}]})

    out = _self_hosted_inpaint(monkeypatch, Image.new('RGB', (32, 32), 'red'),
                               None, {}, respond, include_mask=False)
    assert 'image' in out, out
    assert seen.get('size') == '32x32'
    # The synthesized mask is image-sized and all-white (whole image editable).
    mask_img = Image.open(io.BytesIO(mask_bytes['png'])).convert('L')
    assert mask_img.size == (32, 32)
    assert mask_img.getextrema() == (255, 255)


def test_openai_inpaint_without_mask_is_a_clean_400(monkeypatch):
    """OpenAI edits need a mask; a missing one must be a clean 400, not a
    KeyError traceback."""
    from fastapi import HTTPException

    endpoint = SimpleNamespace(base_url='https://api.openai.com/v1', api_key='sk-x', owner='alice')
    monkeypatch.setattr(gallery, 'SessionLocal', lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(gallery, 'require_privilege', lambda *a: 'alice')
    monkeypatch.setattr(gallery, '_visible_image_endpoint_for_base', lambda *a: endpoint)
    monkeypatch.setattr('src.url_safety.check_outbound_url', lambda *a, **k: (True, ''))

    class Request:
        async def json(self):
            return {'_endpoint': 'https://api.openai.com/v1',
                    'image': png(Image.new('RGB', (32, 32), 'red')), 'prompt': 'x'}

    route = next(r for r in gallery.setup_gallery_routes().routes if r.path == '/api/image/inpaint')
    try:
        asyncio.run(route.endpoint(Request()))
        raise AssertionError('expected a 400 for a missing mask')
    except HTTPException as exc:
        assert exc.status_code == 400
        assert 'mask' in str(exc.detail).lower()


def test_self_hosted_inpaint_forwards_request_id(monkeypatch):
    """A client-supplied request_id is forwarded to the provider so the client
    can poll GET /api/image/edit-progress for step progress."""
    seen = {}

    def respond(request):
        seen.update(_form_fields(request.content, request.headers.get('content-type', '')))
        return httpx.Response(200, json={'model': 'qwen-image-edit-2511',
                                         'data': [{'b64_json': png(Image.new('RGBA', (32, 32), 'red'))}]})

    out = _self_hosted_inpaint(monkeypatch, Image.new('RGB', (32, 32), 'red'),
                               Image.new('L', (32, 32), 255), {'request_id': 'job-777'}, respond)
    assert 'image' in out, out
    assert seen.get('request_id') == 'job-777'
