from routes.gallery_routes import _norm_image_endpoint_url


def test_gallery_url_normalization_bug():
    # Exact suffix removal must strip only a real trailing /v1 segment, not
    # arbitrary trailing characters such as rstrip('/v1') would do.
    assert _norm_image_endpoint_url("http://localhost:8000/v1") == "http://localhost:8000"
    assert _norm_image_endpoint_url("http://localhost:8000") == "http://localhost:8000"
    assert _norm_image_endpoint_url("http://localhost:8000/v1/") == "http://localhost:8000"

    assert _norm_image_endpoint_url("http://localhost:8000/v11") == "http://localhost:8000/v11"
    assert _norm_image_endpoint_url("http://localhost:8000/dev1") == "http://localhost:8000/dev1"
