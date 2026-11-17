"""CUDA detection, runtime classification and VRAM-safe offload planning (no model, no GPU needed)."""
import os
import struct

import pytest

from src import gpu_runtime
from tests.test_gguf_context_planning import metadata_file, text
from tests.test_model_serving_control import free_port

GIB = 1024 ** 3
EXE = 'llama-server.exe' if os.name == 'nt' else 'llama-server'


def cuda_dir(tmp_path, libs=('cudart64_13.dll', 'cublas64_13.dll'), present=()):
    (tmp_path / EXE).write_bytes(b'placeholder')
    imports = b'\0'.join(name.encode() for name in libs)
    (tmp_path / 'ggml-cuda.dll').write_bytes(b'MZ\0\0' + imports + b'\0tail')
    for name in present:
        (tmp_path / name).write_bytes(b'lib')
    return tmp_path


def gguf(tmp_path, layers=32):
    return metadata_file(tmp_path / 'm.gguf', [
        ('general.architecture', 8, text('qwen2')),
        ('qwen2.context_length', 4, struct.pack('<I', 32768)),
        ('qwen2.block_count', 4, struct.pack('<I', layers)),
        ('qwen2.embedding_length', 4, struct.pack('<I', 4096)),
        ('qwen2.attention.head_count', 4, struct.pack('<I', 32)),
        ('qwen2.attention.head_count_kv', 4, struct.pack('<I', 8)),
    ])


def test_wmi_driver_string_translates_to_nvidia_number():
    assert gpu_runtime.nvidia_driver_from_wmi('32.0.16.2101') == '621.01'
    assert gpu_runtime.nvidia_driver_from_wmi('32.0.15.6094') == '560.94'
    assert gpu_runtime.nvidia_driver_from_wmi('garbage') == ''


def test_cpu_only_runtime_is_classified_cpu(tmp_path):
    (tmp_path / EXE).write_bytes(b'x')
    assert gpu_runtime.inspect_runtime(tmp_path)['build'] == 'cpu'


def test_cuda_backend_without_runtime_libs_is_not_ready(tmp_path):
    info = gpu_runtime.inspect_runtime(cuda_dir(tmp_path), cuda_driver_major=13)
    assert info['build'] == 'cuda' and not info['cuda_ready']
    assert info['missing_libs'] == ['cublas64_13.dll', 'cudart64_13.dll']


def test_cuda_runtime_needing_newer_cuda_than_driver_is_not_ready(tmp_path):
    cuda_dir(tmp_path, present=('cudart64_13.dll', 'cublas64_13.dll'))
    info = gpu_runtime.inspect_runtime(tmp_path, cuda_driver_major=12)
    assert not info['cuda_ready'] and 'CUDA 13' in info['detail']
    assert gpu_runtime.inspect_runtime(tmp_path, cuda_driver_major=13)['cuda_ready']


def test_complete_cuda_runtime_copies_only_missing_and_never_overwrites(tmp_path):
    target, source = tmp_path / 'install', tmp_path / 'runtime'
    target.mkdir(), source.mkdir()
    cuda_dir(target, present=('cudart64_13.dll',))
    (target / 'cudart64_13.dll').write_bytes(b'keep-me')
    (source / 'cudart64_13.dll').write_bytes(b'other')
    (source / 'cublas64_13.dll').write_bytes(b'cublas-bytes')
    result = gpu_runtime.complete_cuda_runtime(target, source)
    assert result == {'ok': True, 'copied': ['cublas64_13.dll'], 'error': None}
    assert (target / 'cudart64_13.dll').read_bytes() == b'keep-me'
    assert gpu_runtime.inspect_runtime(target)['cuda_ready']
    assert not list(target.glob('*.part'))


def test_complete_cuda_runtime_reports_missing_source_without_partial_state(tmp_path):
    target, source = tmp_path / 'install', tmp_path / 'runtime'
    target.mkdir(), source.mkdir()
    cuda_dir(target)
    result = gpu_runtime.complete_cuda_runtime(target, source)
    assert not result['ok'] and 'not found' in result['error']
    assert not list(target.glob('*.part'))


def test_driver_probe_reports_unavailable_when_library_missing():
    def loader():
        raise OSError('nvcuda.dll not found')
    result = gpu_runtime.probe_cuda_driver(loader)
    assert result['available'] is False and 'not found' in result['error']


def test_offload_clamps_to_vram_with_headroom_on_8gb_card():
    plan = gpu_runtime.plan_gpu_offload(
        requested_layers=999, model_bytes=int(14 * GIB), layers=40, kv_bytes_per_token=160 * 1024,
        context=4096, free_vram=int(7.1 * GIB), total_vram=8 * GIB)
    assert plan['clamped'] and 0 < plan['gpu_layers'] < 40
    used = plan['gpu_layers'] * plan['per_layer_bytes']
    assert used <= 7.1 * GIB - plan['reserve_bytes']


def test_small_model_is_fully_offloaded():
    plan = gpu_runtime.plan_gpu_offload(
        requested_layers=999, model_bytes=int(2 * GIB), layers=28, kv_bytes_per_token=28 * 1024,
        context=4096, free_vram=int(7 * GIB), total_vram=8 * GIB)
    assert plan['fully_offloaded'] and not plan['clamped'] and plan['gpu_layers'] == 28


def test_plan_refuses_gpu_layers_on_cpu_only_runtime(tmp_path):
    from src.model_serving_control import build_launch_plan
    (tmp_path / EXE).write_bytes(b'x')
    with pytest.raises(ValueError, match='CPU-only'):
        build_launch_plan(engine='llamacpp', executable=str(tmp_path / EXE), model_path=str(gguf(tmp_path)),
                          port=free_port(), gpu_layers=20,
                          gpu={'available': True, 'free_bytes': 7 * GIB, 'total_bytes': 8 * GIB})


def test_plan_refuses_incomplete_cuda_runtime(tmp_path):
    from src.model_serving_control import build_launch_plan
    cuda_dir(tmp_path)
    with pytest.raises(ValueError, match='missing'):
        build_launch_plan(engine='llamacpp', executable=str(tmp_path / EXE), model_path=str(gguf(tmp_path)),
                          port=free_port(), gpu_layers=20,
                          gpu={'available': True, 'cuda_driver_major': 13, 'free_bytes': 7 * GIB, 'total_bytes': 8 * GIB})


def test_plan_refuses_gpu_layers_without_detected_gpu(tmp_path):
    from src.model_serving_control import build_launch_plan
    cuda_dir(tmp_path, present=('cudart64_13.dll', 'cublas64_13.dll'))
    with pytest.raises(ValueError, match='no usable NVIDIA GPU'):
        build_launch_plan(engine='llamacpp', executable=str(tmp_path / EXE), model_path=str(gguf(tmp_path)),
                          port=free_port(), gpu_layers=20, gpu={'available': False})


def test_plan_clamps_gpu_layers_to_free_vram_and_warns(tmp_path):
    from src.model_serving_control import build_launch_plan
    cuda_dir(tmp_path, present=('cudart64_13.dll', 'cublas64_13.dll'))
    model = gguf(tmp_path)
    with model.open('ab') as handle:
        handle.truncate(6 * GIB)  # sparse placeholder standing in for 6 GiB of tensors
    plan = build_launch_plan(engine='llamacpp', executable=str(tmp_path / EXE), model_path=str(model),
                             port=free_port(), gpu_layers=999, context_length=4096,
                             available_memory_bytes=40 * GIB,
                             gpu={'available': True, 'cuda_driver_major': 13, 'free_bytes': 4 * GIB,
                                  'total_bytes': 8 * GIB})
    assert 0 < plan['gpu_layers'] < 32
    assert any('GPU offload reduced' in w for w in plan['warnings'])
    assert plan['memory_validation'] == 'estimated'
    assert str(plan['gpu_layers']) in plan['argv']


def test_status_finds_nested_runtime_and_prefers_ready_newest_cuda(tmp_path):
    ready = tmp_path / 'cuda13' / 'bin'
    ready.mkdir(parents=True)
    cuda_dir(ready, present=('cudart64_13.dll', 'cublas64_13.dll'))
    broken = tmp_path / 'cuda12'
    broken.mkdir()
    cuda_dir(broken, libs=('cudart64_12.dll',))
    cpu = tmp_path / 'cpu'
    cpu.mkdir()
    (cpu / EXE).write_bytes(b'x')

    class Driver:
        def __getattr__(self, name):
            return lambda *args: 1  # every call fails: no GPU

    nogpu = gpu_runtime.gpu_status(tmp_path, driver_loader=lambda: Driver())
    assert nogpu['gpu']['available'] is False and nogpu['selected_runtime'] is None
    builds = {r['tag']: (r['build'], r['cuda_ready']) for r in nogpu['runtimes']}
    assert builds == {'cpu': ('cpu', False), 'cuda12': ('cuda', False), 'cuda13': ('cuda', True)}


def test_cuda13_warning_is_dropped_when_driver_reports_cuda13():
    from src.llama_runtime import cuda_driver_warning
    name = 'llama-b1-bin-win-cuda-13.5-x64.zip'
    assert 'newer NVIDIA driver' in cuda_driver_warning(name, '32.0.16.2101')
    assert cuda_driver_warning(name, '621.01', 13) == ''
