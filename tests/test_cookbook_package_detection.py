"""Local Cookbook dependency detection — distribution-name mapping (issue #1020).

The Cookbook → Dependencies tab reported `llama-cpp-python[server]` as "not
installed" even when it was installed. The local check looked up distribution
metadata under `pkg["name"].replace("_", "-")` → "llama-cpp", but the import
module `llama_cpp` ships in the **llama-cpp-python** distribution, so
`importlib.metadata.version("llama-cpp")` raised PackageNotFoundError and the
package was marked missing. The fix derives the distribution name from the
package's declared pip spec instead.
"""

from pathlib import Path

from routes.shell_routes import _pip_dist_name


def test_llama_cpp_maps_to_llama_cpp_python_distribution():
    pkg = {"name": "llama_cpp", "pip": "llama-cpp-python[server]"}
    assert _pip_dist_name(pkg) == "llama-cpp-python"
    # The old behaviour (munging the import name) produced the wrong dist name.
    assert _pip_dist_name(pkg) != "llama-cpp"


def test_extras_and_version_markers_are_stripped():
    assert _pip_dist_name({"name": "diffusers", "pip": "diffusers[torch]"}) == "diffusers"
    assert _pip_dist_name({"name": "transformers", "pip": "transformers"}) == "transformers"
    assert _pip_dist_name({"name": "sglang", "pip": "sglang[all]"}) == "sglang"
    assert _pip_dist_name({"name": "rembg", "pip": "rembg[gpu]"}) == "rembg"
    assert _pip_dist_name({"name": "x", "pip": "foo>=1.2,<2"}) == "foo"
    assert _pip_dist_name({"name": "y", "pip": "bar==1.0 ; python_version>='3.9'"}) == "bar"


def test_plain_names_pass_through():
    assert _pip_dist_name({"name": "vllm", "pip": "vllm"}) == "vllm"
    assert _pip_dist_name({"name": "playwright", "pip": "playwright"}) == "playwright"
    assert _pip_dist_name({"name": "hf_transfer", "pip": "hf_transfer"}) == "hf_transfer"


def test_falls_back_to_import_name_when_no_pip_spec():
    # System rows (tmux/docker) declare no pip spec; fall back to the munged name.
    assert _pip_dist_name({"name": "some_mod", "pip": ""}) == "some-mod"
    assert _pip_dist_name({"name": "tmux"}) == "tmux"


def test_route_uses_dist_name_helper_not_munged_import_name():
    """Lock the wiring: the local package check must look up metadata by the
    derived distribution name, not the old `name.replace('_','-')` (the exact
    bug that hid llama-cpp-python)."""
    src = (Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py").read_text(encoding="utf-8")
    assert "importlib_metadata.version(_pip_dist_name(pkg))" in src
    assert 'importlib_metadata.version(pkg["name"].replace("_", "-"))' not in src


def test_transformers_is_listed_as_image_dependency():
    src = (Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py").read_text(encoding="utf-8")

    assert '"name": "transformers"' in src
    assert '"pip": "transformers"' in src
    assert '"transformers",' in src
