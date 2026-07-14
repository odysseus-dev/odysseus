"""Tests for ControlNet two-pass helpers and image proposal flag."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from titan.control_net_two_pass import (
    resolve_control_net_enabled,
    scheduler_two_pass_generations,
    two_pass_eligible,
)
from titan.image_proposal import build_proposal


def test_build_proposal_carries_control_net():
    prop = build_proposal({"prompt": "scene", "control_net": True})
    assert prop["control_net"] is True


def test_resolve_control_net_prefers_explicit():
    assert resolve_control_net_enabled(
        raw={"control_net": False},
        proposal={"control_net": True},
    ) is False


def test_two_pass_eligible_requires_txt2img_generate():
    proposal = {"style": "anime", "op": "generate"}
    body = {"style": "anime", "n": 1}
    assert two_pass_eligible(op="generate", proposal=proposal, body=body, control_net_enabled=True)
    assert not two_pass_eligible(op="regenerate", proposal=proposal, body=body, control_net_enabled=True)
    assert not two_pass_eligible(
        op="generate",
        proposal={"style": "krea"},
        body={"style": "krea"},
        control_net_enabled=True,
    )
    assert not two_pass_eligible(
        op="generate",
        proposal=proposal,
        body={"image": "abc"},
        control_net_enabled=True,
    )


def test_default_control_net_off_when_unset():
    assert resolve_control_net_enabled(raw={}, proposal={}) is False


@pytest.mark.asyncio
async def test_scheduler_two_pass_runs_both_calls():
    pass1_b64 = "cGFzczE="
    pass2_b64 = "cGFzczI="

    resp1 = MagicMock(status_code=200)
    resp1.json.return_value = {"data": [{"b64_json": pass1_b64}]}
    resp2 = MagicMock(status_code=200)
    resp2.json.return_value = {"data": [{"b64_json": pass2_b64}]}

    client = MagicMock()
    client.post = AsyncMock(side_effect=[resp1, resp2])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("titan.control_net_two_pass.httpx.AsyncClient", return_value=client):
        data, used = await scheduler_two_pass_generations(
            {"prompt": "test", "style": "anime"},
            scheduler_url="http://scheduler",
        )

    assert used is True
    assert data["data"][0]["b64_json"] == pass2_b64
    assert client.post.await_count == 2
    pass1_json = client.post.await_args_list[0].kwargs.get("json") or client.post.await_args_list[0][1].get("json")
    assert pass1_json.get("shutdown_after") is False


@pytest.mark.asyncio
async def test_scheduler_two_pass_fallback_to_pass1():
    pass1_b64 = "cGFzczE="

    resp1 = MagicMock(status_code=200)
    resp1.json.return_value = {"data": [{"b64_json": pass1_b64}]}
    resp2 = MagicMock(status_code=500, text="controlnet missing")

    client = MagicMock()
    client.post = AsyncMock(side_effect=[resp1, resp2])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("titan.control_net_two_pass.httpx.AsyncClient", return_value=client):
        data, used = await scheduler_two_pass_generations(
            {"prompt": "test", "style": "anime"},
            scheduler_url="http://scheduler",
        )

    assert used is False
    assert data["data"][0]["b64_json"] == pass1_b64
