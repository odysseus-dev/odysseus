"""Metadata-only GGUF fixtures are not inference models and contain no tensors."""
import os
import struct
import pytest

from tests.test_model_serving_control import free_port


def text(value):
    data = value.encode()
    return struct.pack('<Q', len(data)) + data


def metadata_file(path, entries, version=3):
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', version, 0, len(entries)) + b''.join(
        text(key) + struct.pack('<I', kind) + value for key, kind, value in entries))
    return path


def test_plan_caps_caller_context_by_declared_gguf_training_context(tmp_path):
    from src.model_serving_control import build_launch_plan
    executable = tmp_path / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
    executable.write_bytes(b'not executed: runtime placeholder')
    model = metadata_file(tmp_path / 'metadata-only.gguf', [
        ('general.architecture', 8, text('qwen2')),
        ('qwen2.context_length', 4, struct.pack('<I', 4096)),
    ])
    plan = build_launch_plan(engine='llamacpp', executable=str(executable), model_path=str(model),
        port=free_port(), context_length=32768, model_context_limit=16384,
        available_memory_bytes=8 * 1024**3)
    assert plan['context_length'] == 4096, 'A caller hint must not overrule the model-declared context'
    assert plan['declared_context_length'] == 4096
    assert plan['memory_validation'] == 'unknown', 'Declared training context does not prove KV/RAM/VRAM fit'
    assert plan['gpu_layers'] == 0


@pytest.mark.parametrize(('version', 'kind', 'fmt'), [(2, 4, '<I'), (3, 10, '<Q')])
def test_context_reader_skips_vocab_arrays_without_loading_tensors(tmp_path, version, kind, fmt):
    from src.gguf_context import declared_context_length
    path = metadata_file(tmp_path / 'metadata-only.gguf', [
        ('tokenizer.ggml.tokens', 9, struct.pack('<IQ', 8, 2) + text('fixture') + text('tokens')),
        ('general.architecture', 8, text('qwen2')),
        ('qwen2.context_length', kind, struct.pack(fmt, 8192)),
    ], version=version)
    assert declared_context_length(path) == 8192


@pytest.mark.parametrize('case', ['duplicate', 'truncated', 'boolean', 'version', 'huge-array', 'huge-string'])
def test_malformed_or_unbounded_metadata_stays_unknown(tmp_path, case):
    from src.gguf_context import declared_context_length
    entries = [('general.architecture', 8, text('qwen2')),
               ('qwen2.context_length', 4, struct.pack('<I', 4096))]
    if case == 'duplicate':
        entries.append(entries[0])
    elif case == 'boolean':
        entries[-1] = ('qwen2.context_length', 7, b'\x01')
    elif case == 'huge-array':
        entries.append(('tokenizer.ggml.tokens', 9, struct.pack('<IQ', 8, 2**64 - 1)))
    elif case == 'huge-string':
        entries.append(('tokenizer.test', 8, struct.pack('<Q', 2**64 - 1)))
    path = metadata_file(tmp_path / 'metadata-only.gguf', entries, version=99 if case == 'version' else 3)
    if case == 'truncated':
        path.write_bytes(path.read_bytes()[:-1])
    assert declared_context_length(path) is None


def test_byte_budget_is_enforced_before_allocating_a_string(tmp_path):
    from src.gguf_context import declared_context_length
    path = metadata_file(tmp_path / 'metadata-only.gguf', [
        ('general.name', 8, text('x' * 4096)),
        ('general.architecture', 8, text('qwen2')),
        ('qwen2.context_length', 4, struct.pack('<I', 4096)),
    ])
    assert declared_context_length(path, max_bytes=128) is None


@pytest.mark.parametrize('budget', [8 * 1024 * 1024 + 1, float('nan')])
def test_reader_cannot_expand_or_disable_its_hard_budget(tmp_path, budget):
    from src.gguf_context import declared_context_length
    path = metadata_file(tmp_path / 'metadata-only.gguf', [
        ('general.architecture', 8, text('qwen2')),
        ('qwen2.context_length', 4, struct.pack('<I', 4096)),
    ])
    assert declared_context_length(path, max_bytes=budget) is None


def test_plan_uses_model_derived_dense_kv_cap_without_claiming_runtime_fit(tmp_path):
    from src.model_serving_control import build_launch_plan
    executable = tmp_path / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
    executable.touch()
    model = metadata_file(tmp_path / 'metadata-only.gguf', [
        ('general.architecture', 8, text('qwen2')),
        ('qwen2.context_length', 4, struct.pack('<I', 32768)),
        ('qwen2.block_count', 4, struct.pack('<I', 32)),
        ('qwen2.embedding_length', 4, struct.pack('<I', 4096)),
        ('qwen2.attention.head_count', 4, struct.pack('<I', 32)),
        ('qwen2.attention.head_count_kv', 4, struct.pack('<I', 8)),
    ])
    plan = build_launch_plan(engine='llamacpp', executable=str(executable), model_path=str(model),
        port=free_port(), context_length=32768, available_memory_bytes=3 * 1024**3)
    estimate = plan['kv_cache_estimate']
    assert estimate['bytes_per_token'] == 131072
    assert estimate['source'] == 'gguf-dense-f16-assumption'
    assert 128 <= plan['context_length'] < 8192
    assert estimate['bytes_at_planned_context'] == plan['context_length'] * 131072
    assert plan['kv_bytes_per_token'] is None, 'Derived hint must not be persisted as caller knowledge'
    assert plan['memory_validation'] == 'unknown', 'Runtime cache layout/type and scratch remain unverified'
    assert plan['gpu_layers'] == 0
