def test_gallery_url_normalization_bug():
    from routes.gallery_routes import _normalize_image_endpoint_base

    def check_match(ep_url: str, base_url: str) -> bool:
        return (
            _normalize_image_endpoint_base(ep_url)
            == _normalize_image_endpoint_base(base_url)
        )

    # Test cases that SHOULD NOT match under a correct implementation
    # (Buggy rstrip('/v1') logic incorrectly treats these as equal)
    assert check_match("http://localhost:8000/v11", "http://localhost:8000") is False
    assert check_match("http://localhost:8000/dev1", "http://localhost:8000/dev") is False

    # Test cases that SHOULD match under a correct implementation
    assert check_match("http://localhost:8000/v1", "http://localhost:8000") is True
    assert check_match("http://localhost:8000", "http://localhost:8000/v1") is True
    assert check_match("http://localhost:8000/v1/", "http://localhost:8000/v1") is True
