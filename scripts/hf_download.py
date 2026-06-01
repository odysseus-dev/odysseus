#!/usr/bin/env python3
"""Download HuggingFace models with clean pipe-friendly progress output.

Usage:
    python3 scripts/hf_download.py <repo_id> [--include "pattern"]

Prints lines like:
    FILE model.safetensors [########------------] 42% 1.23/2.91GB 156.3MB/s
    DONE /path/to/cached/model
"""
import argparse
import sys
import time
import os


_last_print = {}


class PipeTqdm:
    """Minimal tqdm replacement that prints simple progress lines to stdout."""

    def __init__(self, *args, **kwargs):
        self.iterable = args[0] if args else kwargs.get("iterable")
        self.total = kwargs.get("total", None)
        self.desc = kwargs.get("desc", "")
        self.unit = kwargs.get("unit", "it")
        self.n = 0
        self.start_t = time.time()
        self.disable = False
        self._closed = False

        if self.iterable is not None and self.total is None:
            try:
                self.total = len(self.iterable)
            except (TypeError, AttributeError):
                pass

    def __iter__(self):
        if self.iterable is None:
            return
        for item in self.iterable:
            yield item
            self.update(1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __len__(self):
        return self.total or 0

    def update(self, n=1):
        self.n += n
        total = self.total or 0
        if total == 0:
            return
        now = time.time()
        key = id(self)
        # Throttle to every 0.5s, always print on completion
        if now - _last_print.get(key, 0) < 0.5 and self.n < total:
            return
        _last_print[key] = now

        pct = int(100 * self.n / total)
        elapsed = now - self.start_t
        speed = self.n / elapsed if elapsed > 0 else 0
        desc = (self.desc or "").strip()

        # Format sizes
        if total >= 1024 ** 3:
            done_s = f"{self.n / (1024**3):.2f}"
            total_s = f"{total / (1024**3):.2f}GB"
            speed_s = f"{speed / (1024**2):.1f}MB/s"
        elif total >= 1024 ** 2:
            done_s = f"{self.n / (1024**2):.1f}"
            total_s = f"{total / (1024**2):.1f}MB"
            speed_s = f"{speed / (1024**2):.1f}MB/s"
        else:
            done_s = str(self.n)
            total_s = str(total)
            speed_s = f"{speed:.0f}/s"

        # ASCII progress bar
        bar_len = 20
        filled = int(bar_len * self.n / total)
        bar = "#" * filled + "-" * (bar_len - filled)

        print(f"FILE {desc} [{bar}] {pct}% {done_s}/{total_s} {speed_s}", flush=True)

    def set_description(self, desc=None, refresh=True):
        self.desc = desc or ""

    def set_postfix(self, *args, **kwargs):
        pass

    def set_postfix_str(self, s="", refresh=True):
        pass

    def reset(self, total=None):
        self.n = 0
        if total is not None:
            self.total = total
        self.start_t = time.time()

    def refresh(self):
        pass

    def close(self):
        self._closed = True

    def clear(self):
        pass

    def display(self, msg=None, pos=None):
        pass

    @property
    def format_dict(self):
        return {"n": self.n, "total": self.total, "elapsed": time.time() - self.start_t}


def _patch_tqdm():
    """Replace tqdm everywhere with our pipe-friendly version."""
    import tqdm as tqdm_mod

    # Replace the main class
    tqdm_mod.tqdm = PipeTqdm
    tqdm_mod.auto.tqdm = PipeTqdm

    # huggingface_hub uses tqdm.auto or its own utils.tqdm
    try:
        import huggingface_hub.utils
        huggingface_hub.utils.tqdm = PipeTqdm
        # Also patch the _tqdm module if it exists
        if hasattr(huggingface_hub.utils, "_tqdm"):
            huggingface_hub.utils._tqdm.tqdm = PipeTqdm
    except (ImportError, AttributeError):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_id", help="HuggingFace repo (e.g. meta-llama/Llama-3-8B)")
    parser.add_argument("--include", help="File pattern to include (e.g. '*Q4_K_M*')")
    args = parser.parse_args()

    # Disable HF progress bars (we provide our own)
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"

    # Enable Rust-backed parallel downloader if available — big throughput win.
    # Must be set before importing huggingface_hub.
    try:
        import hf_transfer  # noqa: F401
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
    except ImportError:
        print("HINT uv pip install hf_transfer for faster downloads", flush=True)

    _patch_tqdm()

    from huggingface_hub import snapshot_download

    kwargs = {
        "repo_id": args.repo_id,
        "max_workers": int(os.environ.get("HF_HUB_DOWNLOAD_MAX_WORKERS", "8")),
    }
    if args.include:
        kwargs["allow_patterns"] = [args.include]

    print(f"START {args.repo_id}", flush=True)
    try:
        path = snapshot_download(**kwargs)
        print(f"DONE {path}", flush=True)
    except Exception as e:
        print(f"ERROR {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
