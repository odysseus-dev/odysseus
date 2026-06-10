from services.hwfit.fit import _lookup_bandwidth


def test_m3_max_bandwidth_uses_gpu_cores():
    assert _lookup_bandwidth({"gpu_name": "Apple M3 Max", "gpu_cores": 30}) == 300
    assert _lookup_bandwidth({"gpu_name": "Apple M3 Max", "gpu_cores": 40}) == 400


def test_m4_max_bandwidth_uses_gpu_cores():
    assert _lookup_bandwidth({"gpu_name": "Apple M4 Max", "gpu_cores": 32}) == 410
    assert _lookup_bandwidth({"gpu_name": "Apple M4 Max", "gpu_cores": 40}) == 546


def test_m5_max_bandwidth_uses_gpu_cores():
    assert _lookup_bandwidth({"gpu_name": "Apple M5 Max", "gpu_cores": 32}) == 460
    assert _lookup_bandwidth({"gpu_name": "Apple M5 Max", "gpu_cores": 40}) == 614


def test_apple_max_bandwidth_falls_back_conservatively_without_gpu_cores():
    assert _lookup_bandwidth({"gpu_name": "Apple M3 Max"}) == 300
    assert _lookup_bandwidth({"gpu_name": "Apple M4 Max"}) == 410
    assert _lookup_bandwidth({"gpu_name": "Apple M5 Max"}) == 460


def test_fixed_apple_bandwidth_entries_include_updated_m5_values():
    assert _lookup_bandwidth({"gpu_name": "Apple M5 Pro"}) == 307
    assert _lookup_bandwidth({"gpu_name": "Apple M5"}) == 153
