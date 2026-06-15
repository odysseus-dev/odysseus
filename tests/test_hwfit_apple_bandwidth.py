from services.hwfit.fit import _lookup_apple_bandwidth, _lookup_bandwidth


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


def test_non_apple_gpu_does_not_match_apple_bandwidth():
    """NVIDIA Quadro M4 000 should NOT match Apple bandwidth lookup."""
    assert _lookup_bandwidth({"gpu_name": "NVIDIA Quadro M4 000"}) is None
    assert _lookup_bandwidth({"gpu_name": "NVIDIA Quadro M3 000"}) is None
    assert _lookup_bandwidth({"gpu_name": "NVIDIA Quadro M5 000"}) is None


def test_non_apple_gpu_with_cores_does_not_match():
    """A non-Apple GPU that happens to carry a gpu_cores count must not be
    matched by the APPLE bandwidth path. This asserts the Apple-specific
    matcher directly: _lookup_bandwidth would (correctly) return these cards'
    real bandwidth from the general GPU table (e.g. the RTX 4090's 1008 GB/s),
    which is a different code path and not what this guard is about.
    """
    assert _lookup_apple_bandwidth({"gpu_name": "NVIDIA GeForce RTX 4090", "gpu_cores": 128}) is None
    assert _lookup_apple_bandwidth({"gpu_name": "AMD Radeon RX 9070 XT", "gpu_cores": 64}) is None


def test_apple_string_input_resolves_conservative_tier():
    """Bare-string callers must still get Apple bandwidth. #2564 moved the
    Apple tiers out of the generic GPU table into the dict-only Apple helper,
    so _lookup_bandwidth("Apple M3 Max") (no gpu_cores) regressed to None;
    string inputs now route through the Apple helper and get the conservative
    (lowest) tier for the model."""
    assert _lookup_bandwidth("Apple M3 Max") == 300
    assert _lookup_bandwidth("Apple M4 Max") == 410
    assert _lookup_bandwidth("Apple M5 Max") == 460
    # Non-Apple strings still fall through to the generic table.
    assert _lookup_bandwidth("NVIDIA GeForce RTX 4090") == 1008
    assert _lookup_bandwidth("Totally Unknown GPU") is None
