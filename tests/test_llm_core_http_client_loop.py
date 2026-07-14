"""Regression: httpx client must rebind when asyncio loop changes (Fugassa job worker)."""

from __future__ import annotations

import asyncio

import pytest

from src import llm_core


@pytest.fixture(autouse=True)
def _reset_http_client():
    llm_core._http_client = None
    llm_core._http_client_loop = None
    yield
    llm_core._http_client = None
    llm_core._http_client_loop = None


def test_get_http_client_rebinds_after_loop_closes():
    async def first_loop_client_id():
        return id(llm_core._get_http_client())

    id_a = asyncio.run(first_loop_client_id())
    id_b = asyncio.run(first_loop_client_id())
    assert id_a != id_b


def test_get_http_client_reuses_within_same_loop():
    async def two_clients():
        c1 = llm_core._get_http_client()
        c2 = llm_core._get_http_client()
        return c1 is c2

    assert asyncio.run(two_clients()) is True
