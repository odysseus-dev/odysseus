import routes.gallery_routes as gallery_routes


class _TorchSentinel:
    float32 = object()
    float64 = object()


class _FakeTensor:
    def __init__(self, dtype):
        self.dtype = dtype
        self.to_args = None

    def to(self, *args, **kwargs):
        self.to_args = (args, kwargs)
        return self


def test_model_inputs_to_device_casts_mps_float64_to_float32():
    float_tensor = _FakeTensor(_TorchSentinel.float64)
    int_tensor = _FakeTensor("int64")
    plain_value = object()

    result = gallery_routes._model_inputs_to_device(
        {"points": float_tensor, "labels": int_tensor, "plain": plain_value},
        "mps",
        _TorchSentinel,
    )

    assert result["points"] is float_tensor
    assert float_tensor.to_args == ((), {"device": "mps", "dtype": _TorchSentinel.float32})
    assert int_tensor.to_args == (("mps",), {})
    assert result["plain"] is plain_value

