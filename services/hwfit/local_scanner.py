"""Discover locally available GGUF model files for the hwfit catalog."""

from __future__ import annotations

import os
import re
from pathlib import Path

from services.hwfit.models import QUANT_BYTES_PER_PARAM


_QUANT_RE = re.compile(
    r"(?i)(?:^|[-_.])((?:IQ)?Q[2-8](?:_[A-Z0-9]+){0,2}|F16|F32|BF16)(?:[-_.]|$)"
)
_PARAM_RE = re.compile(r"(?i)(?:^|[-_.])(\d+(?:\.\d+)?)\s*([BM])(?:[-_.]|$)")
_FAMILY_ALIASES = (
    "deepseek",
    "gemma",
    "llama",
    "mistral",
    "mixtral",
    "phi",
    "qwen",
    "starcoder",
    "yi",
)
_CONTEXT_BY_FAMILY = {
    "gemma": 8192,
    "llama": 4096,
    "qwen": 32768,
    "mistral": 32768,
    "mixtral": 32768,
    "deepseek": 65536,
    "phi": 4096,
}


def default_scan_dirs() -> list[Path]:
    """Return the local directories Odysseus should scan for GGUF files."""
    repo_root = Path(__file__).resolve().parents[2]
    paths = [repo_root / "data" / "huggingface"]

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        paths.append(Path(userprofile) / ".lmstudio" / "models")
    paths.append(Path.home() / ".lmstudio" / "models")
    paths.extend(_extra_scan_dirs(os.environ.get("ODYSSEUS_MODEL_SCAN_DIRS", "")))

    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            out.append(path.expanduser())
            seen.add(key)
    return out


def scan_local_gguf(scan_dirs: list[str | Path] | None = None) -> list[dict]:
    """Scan local directories and return hwfit-compatible GGUF model entries."""
    roots = [Path(p).expanduser() for p in (scan_dirs or default_scan_dirs())]
    entries: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for path in root.rglob("*.gguf"):
            if not path.is_file() or _is_mmproj(path):
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            entries.append(_entry_from_file(path))
    entries.sort(key=lambda m: (m.get("family") or "", m.get("name") or ""))
    return entries


def _extra_scan_dirs(raw: str) -> list[Path]:
    """Split configured scan paths while tolerating Windows drive letters."""
    if not raw.strip():
        return []
    if ";" in raw:
        parts = raw.split(";")
    elif ":" in raw:
        # Docker/Linux paths use ':'. Windows drive-letter paths are safer as ';',
        # but this still accepts "D:\Models:E:\More" without splitting drives.
        parts = re.split(r":(?![\\/])", raw)
    else:
        parts = [raw]
    return [Path(p.strip()) for p in parts if p.strip()]


def _entry_from_file(path: Path) -> dict:
    """Build one catalog entry from a GGUF file path."""
    stem = path.stem
    family = _infer_family(stem)
    quant = _infer_quant(stem)
    parameter_count = _infer_parameter_count(stem, quant, path.stat().st_size)
    mmproj = _find_mmproj(path)
    source = {
        "repo": str(path.resolve()),
        "kind": "GGUF",
        "path": str(path.resolve()),
        "filename": path.name,
    }
    if mmproj:
        source["mmproj_path"] = str(mmproj.resolve())

    return {
        "name": f"local/{stem}",
        "provider": "Local GGUF",
        "parameter_count": parameter_count,
        "quantization": quant,
        "quant": quant,
        "family": family,
        "is_gguf": True,
        "gguf_sources": [source],
        "backend": "llamacpp",
        "context_length": _CONTEXT_BY_FAMILY.get(family, 4096),
        "context": _CONTEXT_BY_FAMILY.get(family, 4096),
        "local_path": str(path.resolve()),
        "mmproj_path": str(mmproj.resolve()) if mmproj else "",
        "_source": "local_gguf",
    }


def _infer_family(stem: str) -> str:
    """Infer a known model family from a GGUF filename stem."""
    lower = stem.lower()
    for family in _FAMILY_ALIASES:
        if family in lower:
            return family
    token = re.split(r"[-_.\s]+", lower, maxsplit=1)[0]
    return token or "local"


def _infer_quant(stem: str) -> str:
    """Infer a GGUF quantization tier from a filename stem."""
    match = _QUANT_RE.search(stem)
    if not match:
        return "Q4_K_M"
    return match.group(1).upper()


def _infer_parameter_count(stem: str, quant: str, size_bytes: int) -> str:
    """Infer parameter count from filename, falling back to file size."""
    match = _PARAM_RE.search(stem)
    if match:
        value = float(match.group(1))
        suffix = match.group(2).upper()
        return f"{value:g}{suffix}"

    gib = size_bytes / (1024 ** 3)
    bpp = QUANT_BYTES_PER_PARAM.get(quant, 0.5)
    if bpp <= 0:
        return ""
    return f"{max(gib / bpp, 0.1):.1f}B"


def _is_mmproj(path: Path) -> bool:
    """Return True when a GGUF file is a multimodal projection sidecar."""
    return "mmproj" in path.name.lower()


def _find_mmproj(path: Path) -> Path | None:
    """Find a likely mmproj sidecar in the same directory as a GGUF model."""
    candidates = sorted(path.parent.glob("*mmproj*.gguf"))
    if candidates:
        return candidates[0]
    candidates = sorted(path.parent.glob("*-mmproj.gguf"))
    return candidates[0] if candidates else None
