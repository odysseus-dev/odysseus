"""ImageProposal identity contract (Sprint 3+)."""

from titan.image_proposal import (
    build_proposal,
    normalize_reference_images,
    proposal_to_scheduler_body,
    resolve_ip_method,
)


def test_normalize_reference_images_filters_invalid():
    assert normalize_reference_images(None) == []
    assert normalize_reference_images([{"path": ""}]) == []
    out = normalize_reference_images([{"path": "/a.png", "weight": 0.5, "role": "style"}])
    assert out == [{"path": "/a.png", "weight": 0.5, "role": "style"}]


def test_build_proposal_refs_without_identity_backend():
    prop = build_proposal(
        {
            "prompt": "scene",
            "reference_images": [{"path": "portraits/elara.png", "weight": 0.8}],
        }
    )
    assert prop["ip_method"] is None
    assert len(prop["reference_images"]) == 1
    body = proposal_to_scheduler_body(prop)
    assert body.get("ip_method") is None
    assert body["reference_images"][0]["path"] == "portraits/elara.png"


def test_build_proposal_ignores_unsupported_ip_method():
    prop = build_proposal(
        {
            "prompt": "scene",
            "ip_method": "pulid",
            "reference_images": [{"path": "portraits/elara.png", "weight": 0.8}],
            "ip_weight": 0.9,
        }
    )
    assert prop["ip_method"] is None
    body = proposal_to_scheduler_body(prop)
    assert body.get("ip_method") is None


def test_resolve_ip_method_img2img_fallback():
    assert resolve_ip_method({"strength": 0.4}, reference_images=[]) == "img2img"
    assert resolve_ip_method({}, reference_images=[]) == ""
