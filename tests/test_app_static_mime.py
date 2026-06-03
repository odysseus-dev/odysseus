import ast
import mimetypes
from pathlib import Path


def _load_register_static_mime_types():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register_static_mime_types")
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {"mimetypes": mimetypes}
    exec(compile(module, str(app_path), "exec"), ns)
    return ns["register_static_mime_types"]


def test_register_static_mime_types_restores_js_module_types():
    register_static_mime_types = _load_register_static_mime_types()
    original_js = mimetypes.types_map.get(".js")
    original_mjs = mimetypes.types_map.get(".mjs")
    try:
        mimetypes.types_map[".js"] = "text/plain"
        mimetypes.types_map.pop(".mjs", None)

        register_static_mime_types()

        assert mimetypes.types_map[".js"] == "text/javascript"
        assert mimetypes.types_map[".mjs"] == "application/javascript"
    finally:
        if original_js is None:
            mimetypes.types_map.pop(".js", None)
        else:
            mimetypes.types_map[".js"] = original_js

        if original_mjs is None:
            mimetypes.types_map.pop(".mjs", None)
        else:
            mimetypes.types_map[".mjs"] = original_mjs
