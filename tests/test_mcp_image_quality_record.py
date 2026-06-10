"""MCP image server must record the REQUESTED quality, not a default.

generate_image only set payload["quality"] for gpt-image models, and the
gallery row was written as payload.get("quality", "medium"). For DALL-E
(which has no payload quality field) that always recorded "medium", even
when the user asked for "high"/"low"/"auto". The shared _record_quality
helper now feeds both the payload and the gallery row.
"""
import pytest

pytest.importorskip("mcp")

import mcp_servers.image_gen_server as igs


def test_requested_quality_is_recorded():
    assert igs._record_quality("high") == "high"
    assert igs._record_quality("low") == "low"
    assert igs._record_quality("auto") == "auto"
    assert igs._record_quality("medium") == "medium"


def test_invalid_quality_falls_back_to_medium():
    assert igs._record_quality("bogus") == "medium"
    assert igs._record_quality("") == "medium"
    assert igs._record_quality(None) == "medium"
