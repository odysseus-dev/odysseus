import ast
from pathlib import Path


def _load_norm_helper():
    """Extract `_norm_image_endpoint_url` from gallery_routes.py and exec just
    that function, without importing the module (which pulls heavy runtime deps).

    The endpoint-matching logic now lives in this shared helper, so we test the
    real implementation directly instead of AST-evaluating an inline comparison.
    """
    source_path = Path("routes/gallery_routes.py")
    assert source_path.exists(), "gallery_routes.py could not be found"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_norm_image_endpoint_url"),
        None,
    )
    assert fn is not None, "_norm_image_endpoint_url not found in gallery_routes.py"
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<helper>", "exec"), ns)
    return ns["_norm_image_endpoint_url"]


def test_gallery_url_normalization_bug():
    norm = _load_norm_helper()

    def matches(ep_url: str, base_url: str) -> bool:
        # Mirrors how _owned_image_endpoint compares a stored endpoint's
        # base_url against the caller-supplied URL.
        return norm(ep_url) == norm(base_url)

    # SHOULD NOT match — a naive rstrip('/v1') over-strips and treats these as equal.
    assert matches("http://localhost:8000/v11", "http://localhost:8000") is False
    assert matches("http://localhost:8000/dev1", "http://localhost:8000/dev") is False

    # SHOULD match — /v1 suffix and trailing slash are normalized away.
    assert matches("http://localhost:8000/v1", "http://localhost:8000") is True
    assert matches("http://localhost:8000", "http://localhost:8000/v1") is True
    assert matches("http://localhost:8000/v1/", "http://localhost:8000/v1") is True
