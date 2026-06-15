from services.hwfit.fit import GPU_BANDWIDTH, _lookup_bandwidth


def test_rtx_4090_laptop_resolves_to_laptop_bandwidth():
    """RTX 4090 Laptop GPU must not match the desktop "4090" entry."""
    assert _lookup_bandwidth("NVIDIA GeForce RTX 4090 Laptop GPU") == GPU_BANDWIDTH["4090 laptop"]


def test_rtx_4090_desktop_bandwidth_unchanged():
    """The desktop RTX 4090 entry must still resolve to its own value."""
    assert _lookup_bandwidth("NVIDIA GeForce RTX 4090") == GPU_BANDWIDTH["4090"]
    assert _lookup_bandwidth("NVIDIA GeForce RTX 4090") == 1008


def test_laptop_bandwidth_lower_than_desktop_for_same_model():
    """Laptop variant must have lower bandwidth than its desktop counterpart."""
    assert GPU_BANDWIDTH["4090 laptop"] < GPU_BANDWIDTH["4090"]


def test_dict_lookup_form_also_resolves_laptop_correctly():
    """_lookup_bandwidth accepts both a bare string and a dict with gpu_name."""
    laptop_bw = _lookup_bandwidth({"gpu_name": "NVIDIA GeForce RTX 4090 Laptop GPU"})
    desktop_bw = _lookup_bandwidth({"gpu_name": "NVIDIA GeForce RTX 4090"})
    assert laptop_bw == GPU_BANDWIDTH["4090 laptop"]
    assert desktop_bw == GPU_BANDWIDTH["4090"]
    assert laptop_bw < desktop_bw
