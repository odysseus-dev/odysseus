"""POST /api/documents/export-zip answers 400 for ids that are not a list of document ids."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import routes.document_routes as droutes


class _Req:
    def __init__(self, body):
        self.state = SimpleNamespace(current_user="tester")
        self._body = body

    async def json(self):
        return self._body


def _export_zip():
    router = droutes.setup_document_routes(MagicMock(), None)
    for r in router.routes:
        if getattr(r, "path", None) == "/api/documents/export-zip":
            return r.endpoint
    raise RuntimeError("export-zip route not found")


@pytest.mark.parametrize("body", [[], "x", 5, {}, {"ids": []}, {"ids": None}])
async def test_missing_ids_is_a_400(body):
    with pytest.raises(HTTPException) as exc:
        await _export_zip()(_Req(body))
    assert exc.value.status_code == 400
    assert exc.value.detail == "No documents specified"


@pytest.mark.parametrize("ids", [5, "abc", {"a": 1}, [1, 2], [["a"]], ["a", None]])
async def test_ids_that_are_not_a_list_of_strings_is_a_400(ids):
    with pytest.raises(HTTPException) as exc:
        await _export_zip()(_Req({"ids": ids}))
    assert exc.value.status_code == 400
    assert exc.value.detail == "ids must be a list of document ids"
