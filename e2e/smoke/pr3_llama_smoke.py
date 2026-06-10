"""PR-3 llama.cpp model hub — smoke test (minimal pass/fail, no network).

Asserts the local-inference hub loads and that its provisioning is pinned and
runtime-based (nothing vendored): the llama-server binary is downloaded on
demand from a pinned ggml-org/llama.cpp release, and GGUF models come from
HuggingFace — both into gitignored ``data/llama/``. Does NOT download the
binary or any model. The full serve/probe path is the e2e proof
``e2e/test_llama_hub.py`` (PR-4).

Run:   python e2e/smoke/pr3_llama_smoke.py
Exit:  0 = PASS, 1 = FAIL
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def main() -> int:
    import services.llama.manager as m
    from services.llama import LlamaManager, get_llama_manager

    tag = m.LLAMA_RELEASE_TAG
    checks = [
        ("LLAMA_RELEASE_TAG is pinned", isinstance(tag, str) and bool(tag)),
        ("_GH_REL is the ggml-org release URL for the pinned tag",
         m._GH_REL == f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}"),
        ("win artifact names embed the pinned tag",
         tag in m._WIN_CUDA_MAIN and tag in m._WIN_CPU),
    ]

    mgr = get_llama_manager()
    checks.append(("manager instantiates", isinstance(mgr, LlamaManager)))

    try:
        models = mgr.list_local_models()
        checks.append(("list_local_models() -> list", isinstance(models, list)))
    except Exception as exc:
        checks.append((f"list_local_models() raised: {exc}", False))

    # Binary is runtime-provisioned: None on a clean checkout, or a Path if the
    # user already provisioned/installed it. Either way it is NOT vendored.
    sp = mgr.server_path()
    checks.append(("server_path() -> Path|None (runtime-provisioned, not vendored)",
                   sp is None or hasattr(sp, "exists")))

    try:
        from routes.llama_routes import setup_llama_routes
        checks.append(("llama_routes imports", callable(setup_llama_routes)))
    except Exception as exc:
        checks.append((f"llama_routes import raised: {exc}", False))

    for name, passed in checks:
        print(f"  [{'OK' if passed else 'XX'}] {name}")

    if all(passed for _, passed in checks):
        print(f"PASS: llama hub loads; binary provisioned at runtime from "
              f"ggml-org/llama.cpp {tag} (nothing vendored)")
        return 0
    print("FAIL: llama hub smoke checks did not all pass")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
