"""Guard the llama.cpp Docker pull recipe surfaced in Cookbook → Dependencies.

The upstream repo moved from github.com/ggerganov/llama.cpp to
github.com/ggml-org/llama.cpp. The old GHCR namespace
(ghcr.io/ggerganov/llama.cpp) no longer publishes images, so the
docker variant in the Dependencies panel returned
"failed to resolve reference … not found" when copied verbatim (#4457).
The other llama.cpp reference in routes/cookbook_routes.py already uses
ggml-org; this guards the JS recipe so the two stay aligned.
"""
from pathlib import Path

RECIPES_JS = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "cookbook-deps-recipes.js"
)


def test_llama_cpp_docker_recipe_uses_ggml_org_namespace():
    source = RECIPES_JS.read_text(encoding="utf-8")

    assert "ghcr.io/ggml-org/llama.cpp:server-cuda" in source, (
        "Expected the llama.cpp docker recipe to pull from the ggml-org namespace."
    )
    assert "ghcr.io/ggerganov/llama.cpp" not in source, (
        "The ggerganov GHCR namespace no longer publishes llama.cpp images. "
        "Use ghcr.io/ggml-org/llama.cpp:server-cuda."
    )
