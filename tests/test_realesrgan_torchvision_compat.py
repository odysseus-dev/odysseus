import sys
import types

from src.optional_deps import (
    patch_realesrgan_torchvision_compat,
    prepare_optional_dependency_import,
)


def test_realesrgan_patch_restores_removed_functional_tensor_module(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("torchvision"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    sentinel = object()
    torchvision = types.ModuleType("torchvision")
    transforms = types.ModuleType("torchvision.transforms")
    functional = types.ModuleType("torchvision.transforms.functional")
    functional.rgb_to_grayscale = sentinel
    transforms.functional = functional
    torchvision.transforms = transforms
    monkeypatch.setitem(sys.modules, "torchvision", torchvision)
    monkeypatch.setitem(sys.modules, "torchvision.transforms", transforms)
    monkeypatch.setitem(sys.modules, "torchvision.transforms.functional", functional)

    patch_realesrgan_torchvision_compat()

    shim = sys.modules["torchvision.transforms.functional_tensor"]
    assert shim.rgb_to_grayscale is sentinel
    assert shim.rgb_to_grayscale is functional.rgb_to_grayscale


def test_prepare_optional_dependency_import_scopes_patch_to_realesrgan(monkeypatch):
    import src.optional_deps as optional_deps

    calls = []
    monkeypatch.setattr(
        optional_deps,
        "patch_realesrgan_torchvision_compat",
        lambda: calls.append("patched"),
    )

    prepare_optional_dependency_import("diffusers")
    assert calls == []

    prepare_optional_dependency_import("realesrgan")
    assert calls == ["patched"]
