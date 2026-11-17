import asyncio

import pytest
from fastapi import HTTPException

import routes.gallery_routes as gallery


def route(path, method):
    return next(r for r in gallery.setup_gallery_routes().routes if r.path == path and method in r.methods)


def call(path, method, monkeypatch, body):
    monkeypatch.setattr(gallery, 'get_current_user', lambda request: 'alice')

    class Request:
        async def json(self):
            return body

    return asyncio.run(route(path, method).endpoint(Request(), 'album-1'))


@pytest.mark.parametrize('body', [
    {'name': ''},
    {'name': '   '},
    {'name': 123},
    {'name': ['x']},
    {'description': {'a': 1}},
    [1],
])
def test_album_update_rejects_bad_input(body, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        call('/api/gallery/albums/{album_id}', 'PUT', monkeypatch, body)
    assert exc.value.status_code == 400


@pytest.mark.parametrize('op', ['add', 'remove'])
@pytest.mark.parametrize('body', [
    {'image_ids': 'abc'},
    {'image_ids': 5},
    {'image_ids': None},
    {'image_ids': [{'a': 1}]},
    {'image_ids': ['ok', 1]},
    [1],
])
def test_album_image_ids_must_be_a_list_of_strings(op, body, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        call(f'/api/gallery/albums/{{album_id}}/{op}', 'POST', monkeypatch, body)
    assert exc.value.status_code == 400
