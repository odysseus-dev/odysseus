import ast
from pathlib import Path

def test_gallery_url_normalization_bug():
    # Read and parse the actual source file
    source_path = Path("routes/gallery_routes.py")
    assert source_path.exists(), "gallery_routes.py could not be found"
    
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    
    # Locate the comparison node within harmonize_image that references ep.base_url and base
    compare_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            segment = ast.get_source_segment(source, node) or ""
            if "ep.base_url" in segment and "base" in segment and "_norm_url" not in segment:
                compare_node = node
                break
                
    assert compare_node is not None, "Could not find the ep.base_url vs base comparison inside gallery_routes.py"
    
    # Compile the compare node into an expression
    expr = ast.Expression(body=compare_node)
    compiled_code = compile(expr, "<string>", "eval")
    
    def check_match(ep_url: str, base_url: str) -> bool:
        class MockEP:
            def __init__(self, url):
                self.base_url = url
        return eval(compiled_code, {}, {"ep": MockEP(ep_url), "base": base_url})

    # Test cases that SHOULD NOT match under a correct implementation
    # (Buggy rstrip('/v1') logic incorrectly treats these as equal)
    assert check_match("http://localhost:8000/v11", "http://localhost:8000") is False
    assert check_match("http://localhost:8000/dev1", "http://localhost:8000/dev") is False

    # Test cases that SHOULD match under a correct implementation
    assert check_match("http://localhost:8000/v1", "http://localhost:8000") is True
    assert check_match("http://localhost:8000", "http://localhost:8000/v1") is True
    assert check_match("http://localhost:8000/v1/", "http://localhost:8000/v1") is True
