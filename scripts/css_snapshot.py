#!/usr/bin/env python3
"""Computed-style snapshot harness for the shipped app CSS cascade.

The app CSS is an ordered multi-file cascade whose rendered result depends
on source order: hundreds of selectors are declared more than once and
``!important`` is used throughout. Any restructuring - extracting a block into
its own file, reordering ``<link>`` tags, moving an ``@media`` rule - can
silently change which declaration wins, and nothing else in the suite would
notice.

This module captures ``getComputedStyle`` for a fixed inventory of elements
across pages, viewports, themes and density modes, hashes the result, and
compares it against a committed baseline. It moves no CSS. It only makes a move
falsifiable.

Usage::

    python scripts/css_snapshot.py --check            # compare to the baseline
    python scripts/css_snapshot.py --write-baseline   # re-record it
    python scripts/css_snapshot.py --dump before.json # raw values, for diffing

With no ``--origin`` the script serves the repository over loopback on an
ephemeral port for the duration of the run, so it works standalone. Under
pytest the session static server is reused instead.

To see *which property* moved rather than just which element::

    python scripts/css_snapshot.py --dump after.json
    git stash && python scripts/css_snapshot.py --dump before.json && git stash pop
    diff <(python -m json.tool before.json) <(python -m json.tool after.json)
"""
import argparse
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "tests" / "css_snapshot"
INVENTORY_PATH = SNAPSHOT_DIR / "inventory.json"
BASELINE_PATH = SNAPSHOT_DIR / "baseline.json"
CAPTURE_SCRIPT = SNAPSHOT_DIR / "capture.mjs"

# A capture is ~70 page loads; on a warm checkout it runs in well under a
# minute, but a cold `npx playwright install` machine can be slow to start
# Chromium the first time.
CAPTURE_TIMEOUT_SECONDS = 900

# Hash prefix length. 16 hex characters is 64 bits - far past any accidental
# collision risk for a few thousand entries, and short enough that the baseline
# stays readable in a diff.
HASH_LENGTH = 16


def load_inventory(path=INVENTORY_PATH):
    """Load the checked-in element inventory."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_baseline(path=BASELINE_PATH):
    """Load the committed baseline digest."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:HASH_LENGTH]


def node_available(node="node"):
    """True when the node binary is on PATH."""
    return shutil.which(node) is not None


def playwright_available(node="node", cwd=ROOT):
    """True when node can resolve the playwright package from the repo root.

    Playwright is a devDependency installed by ``npm ci``; a clean checkout
    that has not run it cannot drive a browser at all.
    """
    if not node_available(node):
        return False
    result = subprocess.run(
        [node, "-e", "require.resolve('playwright')"],
        cwd=str(cwd), capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def capture(origin, inventory=None, *, swap_rule=None, variants=None,
            measurement_delay_ms=0, node="node", cwd=ROOT,
            timeout=CAPTURE_TIMEOUT_SECONDS):
    """Drive the browser capture and return ``{"snapshot": ..., "missing": ...}``.

    ``swap_rule`` swaps the first two top-level declarations of one selector
    before the stylesheet reaches the browser. It exists for the harness
    self-test: a snapshot that does not move when two conflicting rules trade
    places is not evidence of anything.

    ``variants`` restricts the run to the named variants, for a faster
    focused capture.

    ``measurement_delay_ms`` perturbs the capture timing for the determinism
    self-test; elapsed wall time must not change an idle-state snapshot.
    """
    inventory = inventory or load_inventory()
    selected = inventory["variants"]
    if variants:
        wanted = set(variants)
        selected = [v for v in selected if v["name"] in wanted]
        unknown = wanted - {v["name"] for v in inventory["variants"]}
        if unknown:
            raise ValueError(f"unknown variants: {sorted(unknown)}")
    job = {
        "origin": origin.rstrip("/"),
        "properties": inventory["properties"],
        "variants": selected,
        "pages": inventory["pages"],
        "swapRule": swap_rule,
        "measurementDelayMs": measurement_delay_ms,
    }
    result = subprocess.run(
        [node, str(CAPTURE_SCRIPT)],
        input=json.dumps(job), cwd=str(cwd),
        capture_output=True, text=True, check=False, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"css snapshot capture failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def summarize(snapshot):
    """Reduce a raw capture to the committed digest shape.

    Two orthogonal projections are stored rather than one hash per
    (element, variant) pair: hashing every pair would commit ~5,000 lines that
    nobody reads, while a single global digest would only ever say "something
    moved". Per-element and per-variant hashes localise a failure from both
    directions - which element drifted, and in which variant - for a file small
    enough to review.
    """
    elements = {}
    variants = {}
    for page, per_variant in snapshot.items():
        element_values = {}
        variants[page] = {}
        for variant, measured in per_variant.items():
            variants[page][variant] = _hash(measured)
            for key, values in measured.items():
                element_values.setdefault(key, {})[variant] = values
        elements[page] = {key: _hash(values) for key, values in element_values.items()}
    return {
        "digest": _hash(snapshot),
        "elements": elements,
        "variants": variants,
    }


def compare(baseline, current):
    """Return the drift between a committed baseline and a fresh summary."""
    drift = {"digest_changed": baseline.get("digest") != current["digest"],
             "elements": [], "variants": []}
    for section in ("elements", "variants"):
        old = baseline.get(section, {})
        new = current.get(section, {})
        for page in sorted(set(old) | set(new)):
            old_page = old.get(page, {})
            new_page = new.get(page, {})
            for key in sorted(set(old_page) | set(new_page)):
                if old_page.get(key) != new_page.get(key):
                    drift[section].append(f"{page}/{key}")
    return drift


def serve_repository(root=ROOT):
    """Serve the repository over loopback on an ephemeral port.

    Mirrors the browser-test static server in ``tests/conftest.py`` so the CLI
    can run outside pytest. Returns ``(origin, shutdown)``.
    """
    root = Path(root).resolve()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, fmt, *args):
            pass

        def guess_type(self, path):
            if path.endswith(".js") or path.endswith(".mjs"):
                return "application/javascript"
            if path.endswith(".css"):
                return "text/css"
            return super().guess_type(path)

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def shutdown():
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{server.server_address[1]}", shutdown


def _describe(drift, limit=25):
    lines = []
    for section in ("elements", "variants"):
        items = drift[section]
        if not items:
            continue
        shown = items[:limit]
        suffix = f" (+{len(items) - limit} more)" if len(items) > limit else ""
        lines.append(f"  {section} that moved ({len(items)}): {', '.join(shown)}{suffix}")
    return "\n".join(lines) or "  (no per-element drift; the digest itself changed)"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--origin", help="static server origin to capture against; "
                                         "one is started on an ephemeral port when omitted")
    parser.add_argument("--write-baseline", action="store_true",
                        help=f"re-record {BASELINE_PATH.relative_to(ROOT)}")
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed baseline (default)")
    parser.add_argument("--dump", metavar="PATH",
                        help="write the raw computed values, for property-level diffing")
    parser.add_argument("--swap-rule", metavar="SELECTOR",
                        help="swap the first two top-level declarations of SELECTOR "
                             "before capturing (harness self-test)")
    parser.add_argument("--variants", help="comma-separated variant names to restrict the run to")
    parser.add_argument("--node", default="node", help="node binary to use")
    args = parser.parse_args(argv)

    if not playwright_available(args.node):
        parser.error("node with the playwright package is required; run `npm ci` first")

    variants = [v.strip() for v in args.variants.split(",")] if args.variants else None
    shutdown = None
    origin = args.origin or os.environ.get("ODYSSEUS_TEST_STATIC_ORIGIN")
    if not origin:
        origin, shutdown = serve_repository()
    try:
        captured = capture(origin, swap_rule=args.swap_rule, variants=variants, node=args.node)
    finally:
        if shutdown:
            shutdown()

    if captured["missing"]:
        print("inventory entries that matched no element:", file=sys.stderr)
        for scope, keys in sorted(captured["missing"].items()):
            print(f"  {scope}: {', '.join(keys)}", file=sys.stderr)

    summary = summarize(captured["snapshot"])

    if args.dump:
        Path(args.dump).write_text(json.dumps(captured["snapshot"], indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")
        print(f"raw values written to {args.dump}")

    if args.write_baseline:
        if variants or args.swap_rule:
            parser.error("--write-baseline needs a full, unmutated capture: "
                         "drop --variants and --swap-rule")
        BASELINE_PATH.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"baseline written: digest {summary['digest']}")
        return 0

    baseline = load_baseline()
    drift = compare(baseline, summary)
    if not drift["digest_changed"] and not drift["elements"] and not drift["variants"]:
        print(f"computed styles match the baseline (digest {summary['digest']})")
        return 0
    print(f"computed styles moved: baseline {baseline.get('digest')} -> {summary['digest']}")
    print(_describe(drift))
    return 1


if __name__ == "__main__":
    sys.exit(main())
