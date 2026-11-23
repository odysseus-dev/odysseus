"""The CalDAV config routes answer 400 for a body that is JSON but not an object."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.calendar_routes import _json_object_body


def _request(raw: bytes):
    async def _json():
        return json.loads(raw)
    return SimpleNamespace(json=_json)


def _parse(raw: bytes):
    return asyncio.run(_json_object_body(_request(raw)))


def test_object_body_is_returned():
    assert _parse(b'{"url": "https://dav.example/"}') == {"url": "https://dav.example/"}


@pytest.mark.parametrize("raw", [b"", b"not json"])
def test_unreadable_body_counts_as_empty(raw):
    assert _parse(raw) == {}


@pytest.mark.parametrize("raw", [b"[]", b'"x"', b"5", b"null"])
def test_non_object_body_is_a_400(raw):
    with pytest.raises(HTTPException) as exc:
        _parse(raw)
    assert exc.value.status_code == 400
