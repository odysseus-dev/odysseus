import pathlib
import subprocess

from huggingface_hub.utils._paths import filter_repo_objects
from routes.cookbook_helpers import _validate_include


def _extract_js_helpers() -> str:
    src = pathlib.Path("static/js/cookbook.js").read_text(encoding="utf-8")
    start = src.index("function _ggufQuantFromPath(path)")
    end = src.index("function _ollamaName(raw)")
    return src[start:end]


def test_split_repo_tag_and_gguf_include_via_node():
    helpers = _extract_js_helpers()
    node_script = f"""
    const assert = require('node:assert');
    {helpers}

    // 1. _splitRepoTag produces boundary-anchored globs and passes backend _validate_include
    const q6 = _splitRepoTag('bartowski/Qwen2.5-Coder-7B-Instruct-GGUF:Q6_K');
    assert.strictEqual(q6.repo, 'bartowski/Qwen2.5-Coder-7B-Instruct-GGUF');
    assert.strictEqual(q6.include, '*[-._]Q6_K[.-]*gguf');

    const q4 = _splitRepoTag('org/model-GGUF:Q4_K');
    assert.strictEqual(q4.include, '*[-._]Q4_K[.-]*gguf');

    // Explicit user wildcard globs are preserved untouched
    const custom = _splitRepoTag('org/model-GGUF:*Q4_K_M*.gguf');
    assert.strictEqual(custom.include, '*Q4_K_M*.gguf');

    // 2. _ggufIncludeForQuant resolves exact filenames for single-file quants
    const bartowskiFiles = [
      'Qwen2.5-Coder-7B-Instruct-Q4_K.gguf',
      'Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf',
      'Qwen2.5-Coder-7B-Instruct-Q4_K_S.gguf',
      'Qwen2.5-Coder-7B-Instruct-Q6_K.gguf',
      'Qwen2.5-Coder-7B-Instruct-Q6_K_L.gguf',
      'Qwen2.5-Coder-7B-Instruct-Q8_0.gguf',
      'Qwen2.5-Coder-7B-Instruct-Q8_0_L.gguf',
    ];
    assert.strictEqual(
      _ggufIncludeForQuant(bartowskiFiles, 'Q6_K'),
      'Qwen2.5-Coder-7B-Instruct-Q6_K.gguf'
    );
    assert.strictEqual(
      _ggufIncludeForQuant(bartowskiFiles, 'Q4_K'),
      'Qwen2.5-Coder-7B-Instruct-Q4_K.gguf'
    );

    // 3. Multi-shard files in a quant folder use folder/*.gguf
    const folderShards = [
      'Q6_K/DeepSeek-Q6_K-00001-of-00002.gguf',
      'Q6_K/DeepSeek-Q6_K-00002-of-00002.gguf',
      'Q6_K_L/DeepSeek-Q6_K_L-00001-of-00002.gguf',
      'Q6_K_L/DeepSeek-Q6_K_L-00002-of-00002.gguf',
    ];
    assert.strictEqual(_ggufIncludeForQuant(folderShards, 'Q6_K'), 'Q6_K/*.gguf');

    // 4. Flat multi-shard files use boundary-anchored glob
    const flatShards = [
      'Model-Q6_K-00001-of-00002.gguf',
      'Model-Q6_K-00002-of-00002.gguf',
      'Model-Q6_K_L-00001-of-00002.gguf',
      'Model-Q6_K_L-00002-of-00002.gguf',
    ];
    assert.strictEqual(_ggufIncludeForQuant(flatShards, 'Q6_K'), '*[-._]Q6_K[.-]*gguf');

    console.log(JSON.stringify({{
      q6Include: q6.include,
      exactSingle: _ggufIncludeForQuant(bartowskiFiles, 'Q6_K'),
      folderShard: _ggufIncludeForQuant(folderShards, 'Q6_K'),
      flatShard: _ggufIncludeForQuant(flatShards, 'Q6_K'),
    }}));
    """
    proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_boundary_glob_excludes_sibling_quants_in_huggingface_hub():
    repo_files = [
        "README.md",
        "Qwen2.5-Coder-7B-Instruct-Q4_K.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q4_K_S.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q4_K_L.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q5_K_M_L.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q6_K.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q6_K_L.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q8_0.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q8_0_L.gguf",
    ]

    for quant, expected in [
        ("Q6_K", ["Qwen2.5-Coder-7B-Instruct-Q6_K.gguf"]),
        ("Q4_K", ["Qwen2.5-Coder-7B-Instruct-Q4_K.gguf"]),
        ("Q5_K_M", ["Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf"]),
        ("Q8_0", ["Qwen2.5-Coder-7B-Instruct-Q8_0.gguf"]),
    ]:
        pattern = f"*[-._]{quant}[.-]*gguf"
        assert _validate_include(pattern) == pattern
        matched = list(filter_repo_objects(repo_files, allow_patterns=[pattern]))
        assert matched == expected

    # Split-file shards and dot-delimited names also match without pulling *_L shards
    sharded_files = [
        "Model-Q6_K-00001-of-00002.gguf",
        "Model-Q6_K-00002-of-00002.gguf",
        "Model-Q6_K_L-00001-of-00002.gguf",
        "Model-Q6_K_L-00002-of-00002.gguf",
        "Alt.Q6_K.gguf",
        "Alt.Q6_K_L.gguf",
    ]
    matched_shards = list(filter_repo_objects(sharded_files, allow_patterns=["*[-._]Q6_K[.-]*gguf"]))
    assert matched_shards == [
        "Model-Q6_K-00001-of-00002.gguf",
        "Model-Q6_K-00002-of-00002.gguf",
        "Alt.Q6_K.gguf",
    ]
