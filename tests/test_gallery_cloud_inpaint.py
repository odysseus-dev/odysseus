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
