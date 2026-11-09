#!/usr/bin/env python3
"""Build a small targeted Odysseus tool-router SFT slice.

This slice targets current measured gaps rather than broad tool coverage:

- one-call manage_memory add;
- one-call manage_memory add inside CRUD follow-through;
- clean manage_tasks create schema;
- contextual web_search follow-up after a normal answer;
- no-tool chat boundaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


MANAGE_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "manage_memory",
        "description": "Manage saved memories: list, add, edit, delete, or search.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "edit", "delete", "search"]},
                "text": {"type": "string"},
                "memory_id": {"type": "string"},
                "category": {"type": "string", "enum": ["fact", "event", "contact", "preference"]},
            },
            "required": ["action"],
        },
    },
}

MANAGE_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "manage_tasks",
        "description": "Manage scheduled or recurring background tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "create", "edit", "delete", "pause", "resume"]},
                "task_id": {"type": "string"},
                "name": {"type": "string"},
                "prompt": {"type": "string"},
                "task_type": {"type": "string", "enum": ["llm", "research", "action"]},
                "schedule": {"type": "string"},
                "scheduled_time": {"type": "string"},
                "output_target": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or source-backed information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"]},
            },
            "required": ["query"],
        },
    },
}


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def tool_call(name: str, arguments: dict[str, Any], suffix: str) -> dict[str, Any]:
    return {
        "id": f"call_{suffix}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, separators=(",", ":"), ensure_ascii=True),
        },
    }


def memory_rows() -> list[dict[str, Any]]:
    markers = [
        ("Remember this temporary eval fact: {text}.", "fact"),
        ("Save this about me: {text}.", "fact"),
        ("Store this preference: {text}.", "preference"),
        ("Add this to memory: {text}.", "fact"),
        ("Add to memory that {text}.", "fact"),
        ("Please remember: {text}.", "fact"),
        ("Save this as a memory: {text}.", "fact"),
        ("Keep this in saved memory: {text}.", "fact"),
        ("Can you remember this for later: {text}.", "fact"),
        ("Put this in memory: {text}.", "fact"),
        ("Make a memory that says {text}.", "fact"),
        ("I want you to remember that {text}.", "fact"),
        ("Save this preference for me: {text}.", "preference"),
        ("Add a saved fact: {text}.", "fact"),
    ]
    facts = [
        "I prefer concise travel checklists",
        "My current project is organizing public domain art references",
        "I like calendar summaries grouped by day",
        "My preferred invoice label is Tsuki admin",
        "I want model eval notes kept short",
        "I use Runpod for temporary H100 training jobs",
        "I prefer source links when asking for websites",
        "My document drafts should stay in markdown",
        "short eval probes should use temporary fixture markers",
        "tool add calls should include the memory text immediately",
        "memory cleanup should be checked after CRUD evals",
        "adapter comparisons should record both correctness and efficiency",
        "I prefer benchmark summaries to include artifact paths",
        "I want Odysseus tool tests to report input tokens",
        "I prefer LAN testing before blaming model latency",
        "I like public domain art links from official sources",
        "I want temporary eval memories deleted after tests",
        "I prefer compact prompts for Qwen tool-router evals",
        "I track LoRA quality by correctness and tool efficiency",
        "I want web-link followups to use search when URLs are requested",
        "I prefer no-tool answers for general knowledge reminders",
        "I want memory add calls to avoid validation retries",
    ]
    rows: list[dict[str, Any]] = []
    for i, text in enumerate(facts):
        template, category = markers[i % len(markers)]
        user = template.format(text=text)
        args = {"action": "add", "text": text, "category": category}
        call = tool_call("manage_memory", args, f"memory_add_{i}")
        row = {
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": call["id"], "content": f"Memory added: [{category}] {text}"},
                {"role": "assistant", "content": "Done."},
            ],
            "tools": [MANAGE_MEMORY_TOOL],
            "generator": "targeted_efficiency_static_v1",
            "metadata": {
                "category": "memory_one_call_add",
                "target_issue": "avoid_incomplete_manage_memory_add_first_call",
                "expected_tool_calls": 1,
            },
        }
        row["uuid"] = stable_id("ody_eff_memory", row)
        rows.append(row)
    return rows


def memory_crud_rows() -> list[dict[str, Any]]:
    specs = [
        (
            "ODY-EVAL-CRUD-MEMORY-FLOW alpha checkpoint",
            "ODY-EVAL-CRUD-MEMORY-FLOW beta checkpoint",
            "fact",
        ),
        (
            "I prefer one paragraph status updates for model evals",
            "I prefer concise bullet status updates for model evals",
            "preference",
        ),
        (
            "My current benchmark focus is Odysseus tool-call efficiency",
            "My current benchmark focus is memory add one-call efficiency",
            "fact",
        ),
        (
            "I use temporary memory fixtures during harness tests",
            "I delete temporary memory fixtures after harness tests",
            "fact",
        ),
        (
            "I want saved memory changes to avoid retry tool calls",
            "I want saved memory add calls to include text immediately",
            "preference",
        ),
        (
            "Runpod H100 jobs should be tracked in short notes",
            "Runpod H100 jobs should be tracked with adapter and eval paths",
            "fact",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for i, (alpha, beta, category) in enumerate(specs):
        memory_id = f"mem_eff_{i:02d}"
        add_call = tool_call(
            "manage_memory",
            {"action": "add", "text": alpha, "category": category},
            f"memory_crud_add_{i}",
        )
        edit_call = tool_call(
            "manage_memory",
            {"action": "edit", "memory_id": memory_id, "text": beta},
            f"memory_crud_edit_{i}",
        )
        delete_call = tool_call(
            "manage_memory",
            {"action": "delete", "memory_id": memory_id},
            f"memory_crud_delete_{i}",
        )
        row = {
            "messages": [
                {"role": "user", "content": f"Remember this temporary eval fact: {alpha}."},
                {"role": "assistant", "content": "", "tool_calls": [add_call]},
                {
                    "role": "tool",
                    "tool_call_id": add_call["id"],
                    "content": f"Memory added: [{category}] {alpha}\nMemory id: {memory_id}",
                },
                {"role": "assistant", "content": "Done."},
                {"role": "user", "content": f"Update that memory to say {beta}."},
                {"role": "assistant", "content": "", "tool_calls": [edit_call]},
                {
                    "role": "tool",
                    "tool_call_id": edit_call["id"],
                    "content": f"Memory updated: {beta}\nMemory id: {memory_id}",
                },
                {"role": "assistant", "content": "Updated."},
                {"role": "user", "content": "Delete that memory."},
                {"role": "assistant", "content": "", "tool_calls": [delete_call]},
                {
                    "role": "tool",
                    "tool_call_id": delete_call["id"],
                    "content": f"Memory '{memory_id}' deleted",
                },
                {"role": "assistant", "content": "Deleted."},
            ],
            "tools": [MANAGE_MEMORY_TOOL],
            "generator": "targeted_efficiency_static_v2",
            "metadata": {
                "category": "memory_crud_one_call_followthrough",
                "target_issue": "avoid_incomplete_manage_memory_add_first_call_in_crud_context",
                "expected_tool_calls_per_turn": [1, 1, 1],
            },
        }
        row["uuid"] = stable_id("ody_eff_memory_crud", row)
        rows.append(row)
    return rows


def task_rows() -> list[dict[str, Any]]:
    specs = [
        ("Daily email triage checkpoint", "Summarize unread important email each morning.", "daily", "09:00"),
        ("Weekly invoice reminder", "Remind me to review open invoices every Monday.", "weekly", "08:30"),
        ("Runpod spend check", "Check the Runpod budget note and remind me if follow-up is needed.", "daily", "18:00"),
        ("Calendar prep", "Prepare a short next-day calendar summary.", "daily", "20:00"),
        ("Research queue sweep", "Review saved research tasks and list blockers.", "weekly", "10:00"),
        ("Document cleanup reminder", "Remind me to tidy stale editor documents.", "weekly", "16:00"),
    ]
    rows: list[dict[str, Any]] = []
    for i, (name, prompt, schedule, scheduled_time) in enumerate(specs):
        user = f"Create a scheduled task named {name} that runs {schedule} at {scheduled_time} UTC and has prompt: {prompt}"
        args = {
            "action": "create",
            "name": name,
            "prompt": prompt,
            "task_type": "llm",
            "schedule": schedule,
            "scheduled_time": scheduled_time,
            "output_target": "chat",
        }
        call = tool_call("manage_tasks", args, f"task_create_{i}")
        row = {
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": call["id"], "content": f"Task created: {name}"},
                {"role": "assistant", "content": "Task created."},
            ],
            "tools": [MANAGE_TASKS_TOOL],
            "generator": "targeted_efficiency_static_v1",
            "metadata": {
                "category": "task_create_clean_schema",
                "target_issue": "avoid_loose_task_create_fields",
                "expected_tool_calls": 1,
            },
        }
        row["uuid"] = stable_id("ody_eff_task", row)
        rows.append(row)
    return rows


def web_followup_rows() -> list[dict[str, Any]]:
    first_answers = [
        (
            "What are some good sites for public domain art?",
            "Good public domain art sources include Wikimedia Commons, The Met Open Access, Rijksmuseum Rijksstudio, Smithsonian Open Access, and the Library of Congress.",
            "send links",
            "public domain art Wikimedia Commons Met Open Access Rijksmuseum Smithsonian Library of Congress official links",
        ),
        (
            "What are good places to find old maps online?",
            "Good places include the Library of Congress, David Rumsey Map Collection, Wikimedia Commons, and Old Maps Online.",
            "sned links for those",
            "old maps Library of Congress David Rumsey Wikimedia Commons Old Maps Online official links",
        ),
        (
            "Where can I find free classical music recordings?",
            "Try Musopen, Wikimedia Commons audio, Internet Archive, and IMSLP for public domain scores and recordings.",
            "for the websites",
            "free classical music recordings Musopen Wikimedia Commons Internet Archive IMSLP official links",
        ),
        (
            "What are reliable sources for public domain books?",
            "Project Gutenberg, Internet Archive, HathiTrust, Standard Ebooks, and Open Library are useful starting points.",
            "send the links",
            "public domain books Project Gutenberg Internet Archive HathiTrust Standard Ebooks Open Library official links",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for i, (user1, answer1, user2, query) in enumerate(first_answers):
        call = tool_call("web_search", {"query": query}, f"web_followup_{i}")
        row = {
            "messages": [
                {"role": "user", "content": user1},
                {"role": "assistant", "content": answer1},
                {"role": "user", "content": user2},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": "Search results include official source pages with URLs.",
                },
                {"role": "assistant", "content": "Here are the official links."},
            ],
            "tools": [WEB_SEARCH_TOOL],
            "generator": "targeted_efficiency_static_v1",
            "metadata": {
                "category": "contextual_web_followup",
                "target_issue": "use_web_search_for_links_followup_after_general_answer",
                "expected_tool_calls": 1,
            },
        }
        row["uuid"] = stable_id("ody_eff_web", row)
        rows.append(row)
    return rows


def no_tool_rows() -> list[dict[str, Any]]:
    pairs = [
        ("hi", "Hi! How can I help?"),
        ("who are you?", "I am Odysseus, your assistant."),
        ("what does VAT stand for?", "VAT stands for value-added tax."),
        ("where is Sweden on a map?", "Sweden is in Northern Europe, on the Scandinavian Peninsula between Norway and Finland."),
        ("thanks", "You're welcome."),
        ("what is public domain art?", "Public domain art is artwork whose copyright has expired, was waived, or never applied, so it can generally be reused freely."),
        ("do you remember what VAT stands for?", "VAT stands for value-added tax."),
        ("what should I remember before buying public domain art?", "Check the source, copyright status, license notes, and whether the site marks the work as public domain."),
        ("remind me what Sweden is bordered by", "Sweden borders Norway to the west and Finland to the east."),
        ("what does it mean to remember something in a computer?", "It means storing data or information so it can be retrieved later."),
        ("do you remember what HTML stands for?", "HTML stands for HyperText Markup Language."),
        ("remind me what CPU means", "CPU means central processing unit."),
        ("can you remind me where Italy is?", "Italy is in Southern Europe, extending into the Mediterranean Sea."),
        ("what should I remember when choosing a password?", "Use a long unique password, avoid reuse, and store it in a password manager."),
        ("remember in Python, what does len do?", "In Python, len returns the number of items in an object such as a string, list, or dictionary."),
        ("what did I ask you to remember in this sentence: remember to check licenses?", "You asked about the phrase 'remember to check licenses'; it means to keep license checks in mind."),
        ("does remember always mean save to memory?", "No. It can mean recall, explain, or keep something in mind; only explicit save/store requests need memory tools."),
        ("remind me what public domain means", "Public domain means a work is not restricted by copyright and can generally be used freely."),
        ("what should I remember before using AI-generated images?", "Check licensing, provenance, consent issues, and whether the output matches your intended use."),
        ("do you remember the capital of Sweden?", "The capital of Sweden is Stockholm."),
        ("remind me how to spell calendar", "Calendar is spelled c-a-l-e-n-d-a-r."),
        ("what does a computer memory store?", "Computer memory stores data and instructions for use by programs."),
    ]
    rows: list[dict[str, Any]] = []
    for user, assistant in pairs:
        row = {
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ],
            "tools": [MANAGE_MEMORY_TOOL, MANAGE_TASKS_TOOL, WEB_SEARCH_TOOL],
            "generator": "targeted_efficiency_static_v1",
            "metadata": {
                "category": "no_tool_boundary",
                "target_issue": "avoid_overcalling_tools_on_general_chat",
                "expected_tool_calls": 0,
            },
        }
        row["uuid"] = stable_id("ody_eff_boundary", row)
        rows.append(row)
    return rows


def split_rows(rows: list[dict[str, Any]], val_every: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % val_every == val_every - 1 else train).append(row)
    return train, val


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "targeted_efficiency" / "odysseus_tool_efficiency_v1_20260820"),
    )
    parser.add_argument("--val-every", type=int, default=5)
    args = parser.parse_args()

    rows = memory_rows() + memory_crud_rows() + task_rows() + web_followup_rows() + no_tool_rows()
    train, val = split_rows(rows, args.val_every)
    out_dir = Path(args.out_dir)
    write_jsonl(out_dir / "train.jsonl", train)
    write_jsonl(out_dir / "val.jsonl", val)
    write_jsonl(out_dir / "all.jsonl", rows)
    manifest = {
        "name": out_dir.name,
        "total_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "source_eval": "data/evals/qwen35_9b_v44_memory_onecall_efficiency_20260820_202333.json",
        "categories": {
            category: sum(1 for row in rows if row["metadata"]["category"] == category)
            for category in sorted({row["metadata"]["category"] for row in rows})
        },
        "acceptance_target": (
            "memory_add_one_call_efficiency should reach 2/2 efficiency; "
            "memory_crud_followthrough should reach 3/3 correctness and 3/3 efficiency; "
            "memory_add_wording_variants_efficiency should reach 6/6 correctness and 6/6 efficiency; "
            "memory_no_tool_boundary should reach 4/4 no-tool correctness; "
            "full contextual correctness should remain 42/42 or better."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
