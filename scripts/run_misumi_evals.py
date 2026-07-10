"""Run the small Misumi HTTP behaviour gate against a live Odysseus instance."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "evals" / "misumi" / "fixtures.json"


def _lookup(payload, dotted):
    value = payload
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def evaluate_case(case, payload):
    failures = []
    for key, expected in (case.get("assert") or {}).items():
        actual = _lookup(payload, key)
        if actual != expected:
            failures.append(f"{key}: expected {expected!r}, got {actual!r}")
    for key, choices in (case.get("assert_in") or {}).items():
        actual = _lookup(payload, key)
        if actual not in choices:
            failures.append(f"{key}: expected one of {choices!r}, got {actual!r}")
    for key, expected in (case.get("assert_contains") or {}).items():
        actual = _lookup(payload, key)
        if not isinstance(actual, (list, str)) or expected not in actual:
            failures.append(f"{key}: expected to contain {expected!r}, got {actual!r}")
    for key in case.get("assert_nonempty") or []:
        if not _lookup(payload, key):
            failures.append(f"{key}: expected non-empty value")
    return failures


def request_case(base_url, token, case):
    url = base_url.rstrip("/") + case["path"]
    data = None
    headers = {"Accept": "application/json", "User-Agent": "misumi-eval/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "body" in case:
        data = json.dumps(case["body"]).encode()
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=case["method"])
    with urlopen(request, timeout=30) as response:  # noqa: S310 - operator-selected base URL
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:420")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--include-manual", action="store_true")
    args = parser.parse_args()
    token = os.getenv("ODYSSEUS_API_TOKEN", "")
    cases = json.loads(args.fixtures.read_text(encoding="utf-8"))
    failed = 0
    for case in cases:
        if case.get("manual") and not args.include_manual:
            print(f"SKIP {case['id']} (manual runtime mutation)")
            continue
        try:
            payload = request_case(args.base_url, token, case)
            failures = evaluate_case(case, payload)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            failures = [str(exc)]
        if failures:
            failed += 1
            print(f"FAIL {case['id']}: {'; '.join(failures)}")
        else:
            print(f"PASS {case['id']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
