import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
from PIL import Image

import routes.gallery_routes as gallery


def png(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return base64.b64encode(output.getvalue()).decode()


def test_openrouter_inpaint_keeps_unmasked_pixels(monkeypatch):
    endpoint = SimpleNamespace(base_url='https://openrouter.ai/api/v1', api_key='test')

    class Db:
        def close(self):
            pass

    monkeypatch.setattr(gallery, 'SessionLocal', Db)
    monkeypatch.setattr(gallery, 'require_privilege', lambda *args: 'alice')
    monkeypatch.setattr(gallery, '_visible_image_endpoint_for_base', lambda *args: endpoint)
    monkeypatch.setattr('src.url_safety.check_outbound_url', lambda *args, **kwargs: (True, ''))
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == endpoint.base_url + '/images'
        payload = json.loads(request.content)
        assert payload['model'] == 'openai/gpt-5-image'
        assert len(payload['input_references']) == 2
        assert request.headers['authorization'] == 'Bearer test'
        return httpx.Response(200, json={'data': [{'b64_json': png(Image.new('RGB', (2, 1), 'blue'))}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    mask = Image.new('L', (2, 1))
    mask.putpixel((1, 0), 255)

    class Request:
        async def json(self):
            return {'_endpoint': endpoint.base_url, '_model': 'openai/gpt-5-image',
                    'image': png(Image.new('RGB', (2, 1), 'red')), 'mask': png(mask), 'prompt': 'Make blue'}

    route = next(r for r in gallery.setup_gallery_routes().routes if r.path == '/api/image/inpaint')
    result = asyncio.run(route.endpoint(Request()))
    output = Image.open(io.BytesIO(base64.b64decode(result['image'])))
    assert output.getpixel((0, 0)) == (255, 0, 0, 255)
    assert output.getpixel((1, 0)) == (0, 0, 255, 255)
    assert len(requests) == 1


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
