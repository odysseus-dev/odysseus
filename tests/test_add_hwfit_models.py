import os
import sys
from unittest.mock import MagicMock, patch

# Add repository root to python path to import scripts
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.add_hwfit_models import (
    _estimate_params_from_config,
    _entry_from_modelinfo,
)


def test_estimate_params_llama2_style():
    # Standard Llama 2 style (Gated Silu, MHA, tie embeddings=True)
    config = {
        "vocab_size": 32000,
        "hidden_size": 4096,
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
        "intermediate_size": 11008,
        "hidden_act": "silu",
        "tie_word_embeddings": True,
    }
    total, active = _estimate_params_from_config(config)
    # Total layers: 32
    # Vocab * hidden = 32000 * 4096 = 131,072,000
    # Q, K, V, O attention = 4 * 4096 * 4096 = 67,108,864
    # MLP (Gated) = 3 * 4096 * 11008 = 135,266,304
    # Total per layer = 202,375,168
    # Total layers = 32 * 202,375,168 = 6,476,005,376
    # Total = 131,072,000 + 6,476,005,376 = 6,607,077,376 (~6.6B)
    assert total == 6607077376
    assert active is None  # Non-MoE has active=None


def test_estimate_params_qwen25_style():
    # Qwen 2.5 7B style (GQA, Gated Silu, tie embeddings=False)
    config = {
        "vocab_size": 152064,
        "hidden_size": 3584,
        "num_hidden_layers": 28,
        "num_attention_heads": 28,
        "num_key_value_heads": 4,
        "intermediate_size": 18944,
        "hidden_act": "silu",
        "tie_word_embeddings": False,
    }
    total, active = _estimate_params_from_config(config)
    # Head dim = 3584 // 28 = 128
    # Q = 3584 * (28 * 128) = 12845056
    # K, V = 2 * 3584 * (4 * 128) = 3670016
    # O = 3584 * 3584 = 12845056
    # Attn per layer = 29,360,128
    # MLP (Gated) = 3 * 3584 * 18944 = 203,685,888
    # Layer total = 233,046,016
    # Layers total (28) = 6,525,288,448
    # Embeddings = 152064 * 3584 = 544,997,376
    # Head = 152064 * 3584 = 544,997,376 (since tie embeddings is False)
    # Total = 544,997,376 + 544,997,376 + 6,525,288,448 = 7,615,283,200 (~7.6B)
    assert total == 7615283200
    assert active is None


def test_estimate_params_moe_style():
    # MoE style model (DeepSeek-like or Mixtral-like)
    config = {
        "vocab_size": 32000,
        "hidden_size": 4096,
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
        "intermediate_size": 14336,
        "hidden_act": "silu",
        "num_local_experts": 8,
        "num_experts_per_tok": 2,
        "tie_word_embeddings": True,
    }
    total, active = _estimate_params_from_config(config)
    # Embedding = 32000 * 4096 = 131,072,000
    # Attn per layer = 4 * 4096 * 4096 = 67,108,864
    # Base MLP (Gated) = 3 * 4096 * 14336 = 176,160,768
    # Total MLP (8 experts + router 4096 * 8) = 176,160,768 * 8 + 32,768 = 1,409,318,912
    # Layer total = 67,108,864 + 1,409,318,912 = 1,476,427,776
    # Layers total (32) = 32 * 1,476,427,776 = 47,245,688,832
    # Total parameters = 131,072,000 + 47,245,688,832 = 47,376,760,832 (~47B)
    assert total == 47376760832

    # Active parameters:
    # Active MLP = base MLP * 2 experts + router = 176,160,768 * 2 + 32,768 = 352,354,304
    # Active Layer = 67,108,864 + 352,354,304 = 419,463,168
    # Active Layers total (32) = 32 * 419,463,168 = 13,422,821,376
    # Active total = 131,072,000 + 13,422,821,376 = 13,553,893,376 (~13.5B)
    assert active == 13553893376


def test_estimate_params_non_gated():
    # Standard GPT-2 style non-Gated (GELU, MHA, tie embeddings=True)
    config = {
        "vocab_size": 50257,
        "hidden_size": 768,
        "num_hidden_layers": 12,
        "num_attention_heads": 12,
        "intermediate_size": 3072,
        "hidden_act": "gelu",
        "tie_word_embeddings": True,
    }
    total, active = _estimate_params_from_config(config)
    # Embedding = 50257 * 768 = 38,597,376
    # Attn = 4 * 768 * 768 = 2,359,296
    # MLP (non-Gated) = 2 * 768 * 3072 = 4,718,592
    # Layer total = 7,077,888
    # Layers total (12) = 84,934,656
    # Total = 38,597,376 + 84,934,656 = 123,532,032 (~123M)
    assert total == 123532032
    assert active is None


def test_estimate_params_invalid():
    # Missing hidden_size and num_hidden_layers
    assert _estimate_params_from_config({}) == (None, None)
    assert _estimate_params_from_config(None) == (None, None)


@patch("scripts.add_hwfit_models.hf_hub_download")
def test_entry_from_modelinfo_config_fallback(mock_download):
    # Mock mi (model_info) without size naming or base_model tags
    mi = MagicMock()
    mi.id = "user/non-standard-model-name"
    mi.created_at = None
    mi.pipeline_tag = "text-generation"
    mi.tags = []
    mi.downloads = 100
    mi.likes = 5

    # Mock the hf_hub_download to write a dummy file
    dummy_config = {
        "vocab_size": 32000,
        "hidden_size": 4096,
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
        "intermediate_size": 11008,
        "hidden_act": "silu",
        "tie_word_embeddings": True,
    }
    
    mock_download.return_value = "dummy_config.json"
    
    import json
    from unittest.mock import mock_open
    
    with patch("builtins.open", mock_open(read_data=json.dumps(dummy_config))):
        entry = _entry_from_modelinfo(mi, overrides=None)
        
    assert entry is not None
    # Verify parameter count is inferred from config
    assert entry["parameters_raw"] == 6607077376
    assert entry["parameter_count"] == "6.6B"
    assert entry["quantization"] == "Q4_K_M"
    mock_download.assert_called_once_with(repo_id="user/non-standard-model-name", filename="config.json")
