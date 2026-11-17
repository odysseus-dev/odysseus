#!/usr/bin/env python3
"""Run a small live-model evaluation for exact edit_file routing."""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent_loop import (
    _WORKSPACE_AGENT_TOOLS,
    _looks_like_exact_file_replacement,
    stream_agent_loop,
)


EXACT_TEMPLATES = [
    "In {path}, change status=old to status=new.",
    "Replace `June 30` with `July 1` in {path}.",
    "In {path}, update MODE=dev to MODE=prod.",
    "Change ETA June 30 to ETA July 1 in {path}.",
    "Replace owner=alice with owner=bob in {path}.",
    "In {path}, change enabled=false to enabled=true.",
    "Update color=red to color=green in {path}.",
    "In {path}, replace port=8000 with port=9000.",
    "Change queue=slow to queue=fast in {path}.",
    "Replace draft with published in {path}.",
    "In {path}, update retry=1 to retry=3.",
    "Change region=west to region=east in {path}.",
    "Replace level=info with level=warning in {path}.",
    "In {path}, change feature=off to feature=on.",
    "Update team=alpha to team=beta in {path}.",
    "Replace pending with approved in {path}.",
    "In {path}, change timeout=30 to timeout=60.",
    "Change format=csv to format=json in {path}.",
    "Replace stage=test with stage=production in {path}.",
    "In {path}, update version=1 to version=2.",
]

CONTROL_PREFIXES = [
    "Inspect {path}, then change old_value to new_value.",
    "Read {path} first, then replace old_value with new_value.",
    "Show the contents of {path}, then change old_value to new_value.",
    "Open {path} and replace old_value with new_value.",
    "Review {path} before changing old_value to new_value.",
    "Use cat to inspect {path}, then replace old_value with new_value.",
    "Examine {path}, then update old_value to new_value.",
    "Look at {path} before replacing old_value with new_value.",
    "Change old_value to new_value in {path} and verify the result.",
    "Replace old_value with new_value in {path}, then run the tests.",
]


def _values(template: str) -> tuple[str, str]:
    pairs = [
        ("status=old", "status=new"), ("June 30", "July 1"),
        ("MODE=dev", "MODE=prod"), ("ETA June 30", "ETA July 1"),
        ("owner=alice", "owner=bob"), ("enabled=false", "enabled=true"),
        ("color=red", "color=green"), ("port=8000", "port=9000"),
        ("queue=slow", "queue=fast"), ("draft", "published"),
        ("retry=1", "retry=3"), ("region=west", "region=east"),
        ("level=info", "level=warning"), ("feature=off", "feature=on"),
        ("team=alpha", "team=beta"), ("pending", "approved"),
        ("timeout=30", "timeout=60"), ("format=csv", "format=json"),
        ("stage=test", "stage=production"), ("version=1", "version=2"),
    ]
    return pairs[EXACT_TEMPLATES.index(template)]


def _event(chunk: str):
    if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
        return None
    try:
        return json.loads(chunk[6:])
    except json.JSONDecodeError:
        return None


async def _run_case(endpoint: str, model: str, owner: str, prompt: str, path: Path, expected: str):
    chunks = []
    starts = []
    outputs = []
    stream = stream_agent_loop(
        endpoint,
        model,
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1024,
        max_rounds=4,
        max_tool_calls=4,
        owner=owner,
        workspace=str(path.parent),
        relevant_tools=set(_WORKSPACE_AGENT_TOOLS),
    )
    async for chunk in stream:
        chunks.append(chunk)
        event = _event(chunk)
        if not event:
            continue
        if event.get("type") == "tool_start":
            starts.append(event.get("tool"))
        elif event.get("type") == "tool_output":
            outputs.append(event)
    actual = path.read_text() if path.exists() else ""
    return {
        "classifier_exact": _looks_like_exact_file_replacement(prompt),
        "tool_sequence": starts,
        "tool_outputs": outputs,
        "first_tool": starts[0] if starts else None,
        "content_ok": actual == expected,
        "actual_content": actual,
        "response": "".join(
            event.get("delta", "")
            for chunk in chunks
            if (event := _event(chunk)) and isinstance(event.get("delta"), str)
        ),
    }


async def main(args):
    models_url = args.endpoint.rstrip("/") + "/models"
    try:
        models_response = httpx.get(models_url, timeout=10)
    except httpx.ConnectError:
        # The same eval may run on the host or inside the backend container.
        # Docker's host alias is container-only; use the host-published loopback
        # endpoint when the evaluator is running outside Docker.
        if "host.docker.internal" not in args.endpoint:
            raise
        args.endpoint = args.endpoint.replace("host.docker.internal", "127.0.0.1")
        models_response = httpx.get(args.endpoint.rstrip("/") + "/models", timeout=10)
    models_response.raise_for_status()
    advertised = {
        item.get("id")
        for item in models_response.json().get("data", [])
        if isinstance(item, dict)
    }
    if args.model not in advertised:
        raise SystemExit(
            f"Requested model {args.model!r} is not advertised by the endpoint; "
            f"available={sorted(name for name in advertised if name)}"
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with output.open("w") as handle, tempfile.TemporaryDirectory(prefix="ody-exact-edit-") as root:
        def emit(record):
            records.append(record)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            print(json.dumps(record), flush=True)

        root_path = Path(root)
        for repetition in range(1, args.repetitions + 1):
            exact_templates = EXACT_TEMPLATES[:args.exact_limit] if args.exact_limit else EXACT_TEMPLATES
            for index, template in enumerate(exact_templates, 1):
                old, new = _values(template)
                path = root_path / f"exact_{index}.txt"
                path.write_text(old + "\n")
                prompt = template.format(path=path)
                result = await _run_case(args.endpoint, args.model, args.owner, prompt, path, new + "\n")
                emit({
                    "kind": "exact", "case": index, "repetition": repetition,
                    "model": args.label or args.model, "request_model": args.model,
                    "prompt": prompt, **result,
                })

            control_templates = [] if args.skip_controls else (
                CONTROL_PREFIXES[:args.control_limit] if args.control_limit else CONTROL_PREFIXES
            )
            for index, template in enumerate(control_templates, 1):
                path = root_path / f"control_{index}.txt"
                path.write_text("old_value\n")
                prompt = template.format(path=path)
                result = await _run_case(args.endpoint, args.model, args.owner, prompt, path, "new_value\n")
                emit({
                    "kind": "control", "case": index, "repetition": repetition,
                    "model": args.label or args.model, "request_model": args.model,
                    "prompt": prompt, **result,
                })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--label")
    parser.add_argument("--output", required=True)
    parser.add_argument("--owner", default="pewds")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--exact-limit", type=int, default=0)
    parser.add_argument("--control-limit", type=int, default=0)
    parser.add_argument("--skip-controls", action="store_true")
    asyncio.run(main(parser.parse_args()))
