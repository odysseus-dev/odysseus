import asyncio
import base64
import io

import pytest
from fastapi import HTTPException
from PIL import Image

import routes.gallery_routes as gallery

ROUTES = ['sharpen', 'remove-bg', 'denoise', 'upscale-local', 'enhance-face']


def route(name):
    return next(r for r in gallery.setup_gallery_routes().routes if r.path == f'/api/image/{name}')


def call(name, monkeypatch, body):
    monkeypatch.setattr(gallery, 'require_privilege', lambda *args: 'alice')
    monkeypatch.setattr(gallery, '_local_image_models_enabled', lambda: True)
    monkeypatch.setattr(gallery, '_remote_image_ops_base', lambda: '')

    class Request:
        async def json(self):
            return body

    return asyncio.run(route(name).endpoint(Request()))


@pytest.mark.parametrize('name', ROUTES)
@pytest.mark.parametrize('body', [
    {},
    {'image': '!!notb64'},
    {'image': base64.b64encode(b'hello').decode()},
])
def test_bad_image_is_a_client_error(name, body, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        call(name, monkeypatch, body)
    assert exc.value.status_code == 400


def test_sharpen_still_accepts_a_valid_image(monkeypatch):
    output = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(output, format='PNG')
    result = call('sharpen', monkeypatch, {'image': base64.b64encode(output.getvalue()).decode()})
    assert Image.open(io.BytesIO(base64.b64decode(result['image']))).size == (4, 4)
