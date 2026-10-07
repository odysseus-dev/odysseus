"""
tool_schemas.py

OpenAI-compatible function tool schemas and the converter that turns
native function calls back into ToolBlocks for the execution pipeline.

Extracted from agent_tools.py to keep schema definitions separate from
tool parsing / execution logic.
"""

import json
import logging
import re
import shlex
from typing import Any, Optional

from src.tool_types import ToolBlock, TOOL_TAGS
from src.tool_parsing import _TOOL_NAME_MAP
from src.tool_security import BUILTIN_EMAIL_TOOLS

logger = logging.getLogger(__name__)


_BINARY_VISUAL_MEDIA_SUFFIXES = {
    ".avi", ".bmp", ".gif", ".jpeg", ".jpg", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".png", ".webm", ".webp",
}


_REQUIRED_NATIVE_TOOL_ARGS = {
    "web_search": ("query", "queries"),
    "get_weather": ("location",),
    "web_fetch": ("url", "urls"),
    "pdf_extract": ("url", "path"),
    "private_browser": ("action",),
    "inspect_media": ("path",),
    "extract_text": ("path",),
    "transcribe_media": ("path",),
    "read_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("path",),
    "apply_patch": ("patch_text", "patchText", "patch"),
    "host_shell": ("command",),
}


def _first_nonempty_string(args: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _repair_function_arg_aliases(tool_type: str, args: dict[str, Any]) -> dict[str, Any]:
    """Repair common native-call argument aliases before required-field checks.

    Some local/chat-template wrappers use a generic ``command`` or ``q`` key even
    when the function schema advertises ``query``/``url``. Treat these as
    transport-shape repairs, not semantic rewrites of the model's request.
    """
    if tool_type == "write_file" and not str(args.get("path") or "").strip():
        # The legacy text-tool transport represented a write as
        # ``path\ncontent`` under a generic ``command`` field.  Some native
        # model/template combinations learn that shape from tool results and
        # emit it even when the structured schema advertises separate
        # ``path``/``content`` fields.  Recover only the unambiguous
        # path-plus-newline form; arbitrary one-line commands remain rejected
        # by the required-argument check below.
        command = args.get("command")
        if isinstance(command, str):
            path, separator, content = command.partition("\n")
            if separator and path.strip() and content.strip():
                args["path"] = path.strip()
                args["content"] = content
                logger.warning("Normalized legacy `command` write shape to path/content")
    elif tool_type in {"inspect_media", "extract_text", "transcribe_media"} and not str(args.get("path") or "").strip():
        # Older native media schemas called this field `paths`, sometimes as a
        # JSON-encoded one-element array. The current tools intentionally take
        # one workspace path per call; recovering that transport shape keeps a
        # valid inspection from being discarded without inventing a target.
        path_alias = args.get("paths")
        if isinstance(path_alias, str):
            try:
                decoded = json.loads(path_alias)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = path_alias
            path_alias = decoded
        if isinstance(path_alias, (list, tuple)):
            path_alias = next(
                (item for item in path_alias if isinstance(item, str) and item.strip()),
                None,
            )
        if isinstance(path_alias, str) and path_alias.strip():
            args["path"] = path_alias.strip()
            logger.warning("Normalized legacy `%s` argument to `path` for %s", "paths", tool_type)
    elif tool_type == "web_search" and not str(args.get("query") or "").strip():
        query = _first_nonempty_string(args, ("q", "search_query", "command", "text"))
        if query:
            args["query"] = query
    elif tool_type == "web_fetch" and not str(args.get("url") or "").strip():
        url = _first_nonempty_string(args, ("href", "link", "uri", "command"))
        if url:
            args["url"] = url
    elif tool_type == "pdf_extract" and not str(args.get("url") or "").strip():
        # The public PDF contract historically called every source ``url``,
        # even though local benchmark/user files are paths.  Native models
        # correctly generalize from the rest of the workspace tools and emit
        # ``path``.  Preserve that unambiguous target and normalize only the
        # transport key expected by PdfExtractTool.
        path = _first_nonempty_string(args, ("path", "file_path"))
        if path:
            args["url"] = path
            args.pop("path", None)
            args.pop("file_path", None)
    if tool_type == "manage_documents" and "limit" not in args and "max_results" in args:
        # Collection APIs use both names across the native tool surface. The
        # document contract calls this integer ``limit``.
        args["limit"] = args.pop("max_results")
    if tool_type == "manage_tasks" and str(args.get("action") or "").casefold() == "list":
        # manage_tasks has no backend result-limit argument; the canonical
        # renderer applies the user's visible cap. Drop only these familiar
        # collection aliases so they cannot invalidate an otherwise safe read.
        args.pop("max_results", None)
        args.pop("limit", None)
    return args


def _schema_name_for_tool(name: str, tool_type: str) -> str:
    if name in BUILTIN_EMAIL_TOOLS:
        return name
    if tool_type.startswith("mcp__email__"):
        return tool_type.removeprefix("mcp__email__")
    return tool_type


def _schema_properties_for_tool(name: str, tool_type: str) -> dict[str, dict[str, Any]]:
    schema_name = _schema_name_for_tool(name, tool_type)
    for schema in FUNCTION_TOOL_SCHEMAS:
        function = schema.get("function") if isinstance(schema, dict) else None
        if not isinstance(function, dict) or function.get("name") != schema_name:
            continue
        parameters = function.get("parameters") or {}
        properties = parameters.get("properties") if isinstance(parameters, dict) else None
        return properties if isinstance(properties, dict) else {}
    return {}


def _normalize_function_args_for_schema(name: str, tool_type: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce transport-rendered scalar strings back to schema types.

    Qwen's native/XML template can render boolean parameters as text nodes like
    `False`. The OpenAI-compatible parser then hands that through as the string
    "False", which is a wrapper mismatch, not a model intent error.
    """
    # Compact routers sometimes emit the older action/path shape for the
    # host bridge even though the public contract is command/timeout. Repair
    # only the small, read-only/test vocabulary here; arbitrary actions must
    # still fail closed rather than becoming an invented shell command.
    if tool_type == "host_shell" and not str(args.get("command") or "").strip():
        action = str(args.get("action") or "").strip().lower()
        path = str(args.get("path") or ".").strip()
        if action in {"pytest", "test", "run_tests"} and path:
            args["command"] = f"pytest -q -- {shlex.quote(path)}"
        elif action in {"ls", "list", "list_files"}:
            args["command"] = f"ls -la -- {shlex.quote(path)}"
        elif action in {"pwd", "print_working_directory"}:
            args["command"] = "pwd"
        elif action in {"cat", "read", "read_file"} and path:
            args["command"] = f"sed -n '1,240p' -- {shlex.quote(path)}"

    properties = _schema_properties_for_tool(name, tool_type)
    if not properties:
        return args
    for key, spec in properties.items():
        value = args.get(key)
        if not isinstance(spec, dict):
            continue
        if isinstance(value, str) and spec.get("type") in {"array", "object"}:
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = None
            expected_type = list if spec.get("type") == "array" else dict
            if isinstance(decoded, expected_type):
                args[key] = decoded
                value = decoded
                logger.warning("Normalized JSON-encoded `%s` argument for %s", key, tool_type)
        if not isinstance(value, str):
            if spec.get("type") == "string" and value is not None and not isinstance(value, (dict, list)):
                args[key] = str(value)
            continue
        stripped = value.strip()
        schema_type = spec.get("type")
        if schema_type == "boolean" and stripped.lower() in {"true", "false"}:
            args[key] = stripped.lower() == "true"
        elif schema_type == "integer":
            try:
                args[key] = int(stripped)
            except ValueError:
                pass
        elif schema_type == "number":
            try:
                args[key] = float(stripped)
            except ValueError:
                pass

    # ``exports`` historically described only a batch of timestamped stills.
    # A model asking for one video clip naturally nests the clip's start/end
    # beside its destination in that same collection.  This is unambiguous
    # only for one item, so normalize that shape to the tool's canonical
    # top-level single-clip contract and leave all mixed/batch variants strict.
    if tool_type == "inspect_media":
        exports = args.get("exports")
        if (
            isinstance(exports, list)
            and len(exports) == 1
            and isinstance(exports[0], dict)
            and all(str(exports[0].get(key) or "").strip() for key in ("start", "end", "output_path"))
            and not str(exports[0].get("timestamp") or "").strip()
            and not any(str(args.get(key) or "").strip() for key in ("start", "end", "output_path"))
        ):
            clip = exports[0]
            args["start"] = clip["start"]
            args["end"] = clip["end"]
            args["output_path"] = clip["output_path"]
            for optional in ("speed", "timestamp_path"):
                if optional in clip:
                    args[optional] = clip[optional]
            args.pop("exports", None)
            logger.warning("Normalized one-item inspect_media clip export to top-level range")
    return args


def normalize_native_function_args(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return the canonical tool name and transport-normalized native arguments.

    The clean runtime calls this before schema validation; the converter calls
    it again defensively before producing the legacy ToolBlock transport.
    """
    tool_type = _TOOL_NAME_MAP.get(name, name)
    normalized = _repair_function_arg_aliases(tool_type, dict(args))
    normalized = _normalize_function_args_for_schema(name, tool_type, normalized)
    return tool_type, normalized


def normalized_native_function_argument_error(
    tool_type: str, args: dict[str, Any]
) -> str | None:
    """Return an actionable error for semantic shapes compact schemas can lose.

    Compact schema projection can omit nested ``required`` constraints. Keep
    that transport strict, but tell the model how to recover instead of
    returning an opaque converter failure.
    """
    if tool_type == "pdf_extract":
        source = str(args.get("url") or "").strip()
        if (
            source
            and not source.startswith("/workspace/")
            and not re.match(r"^https?://", source, re.IGNORECASE)
        ):
            return (
                "pdf_extract requires a public http(s) PDF URL or an absolute "
                "/workspace/*.pdf path. A paper title or guessed filename is not a "
                "source; use web_search to discover its real URL, then call pdf_extract."
            )
        return None
    if tool_type != "inspect_media" or not isinstance(args.get("exports"), list):
        return None
    for item in args["exports"]:
        if (
            not isinstance(item, dict)
            or not str(item.get("timestamp") or "").strip()
            or not str(item.get("output_path") or "").strip()
        ):
            return (
                "inspect_media exports only extract video stills and require an explicit "
                "timestamp plus output_path for every item. First inspect the video to find "
                "the timestamp; use write_file or python to author a diagram or other new artifact."
            )
    return None

# ---------------------------------------------------------------------------
# OpenAI-compatible function tool schemas
# ---------------------------------------------------------------------------
FUNCTION_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command (full access). Prefer a dedicated tool whenever one fits the job (reading, writing, editing, searching, or listing files); use bash only for what no dedicated tool covers (installs, git, builds, running programs, system info). Do NOT create or edit files via bash redirects/heredocs/sed -- use the dedicated file tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "host_shell",
            "description": "Run a shell command through an explicitly advertised TUI-host bridge, not inside the backend container. Use only for host/LAN/network tasks where backend Docker/container runtime is limited and the client runtime context says a host bridge is available. Prefer backend bash for normal workspace/server commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The host-side shell command to execute"},
                    "timeout": {"type": "integer", "description": "Optional timeout in seconds, capped by the bridge"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute Python code to compute a result or test something. Prefer a dedicated tool whenever one fits the job (reading, writing, or searching files); use python only for computation, data processing, or scripting no dedicated tool covers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Private quick web lookup through Odysseus' configured search backend, normally SearXNG. Use for open-ended web/search/lookup/latest/current questions. Do NOT navigate to Google/DuckDuckGo/Bing with the browser for search results. NOT for 'research X' / 'do research on X' — those are deep-research jobs; use trigger_research instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "command": {"type": "string", "description": "Search query in text command form"},
                    "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Optional publication-date window for recent articles/news. Omit for current weather, prices, documentation, manuals, or features unless the user specifies a publication window."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current conditions and a three-day forecast for a location from Open-Meteo. No API key. Prefer this over web_search for weather questions.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City or place, optionally with region/country"}},
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and read one or several known HTTP(S) URLs. Use url for one page or urls for up to 12 pages fetched concurrently (four at a time); batch results remain source-separated and output-bounded. For local file:///workspace HTML, use private_browser instead. For a long page or PDF, pass query with model, metric, or table terms to return matching passages instead of the beginning. NOT for open-ended searches (use web_search) or 'research X' jobs (use trigger_research). Downloads are size-budgeted; a '[partial content: ...]' notice means the body was cut short and you can re-call with full=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL or domain to fetch (http/https; a bare domain like example.com is fine)"},
                    "urls": {"type": "array", "minItems": 1, "maxItems": 12, "description": "Known URL strings to fetch concurrently. Use the top-level query to focus every page on the same evidence fields.", "items": {"type": "string"}},
                    "full": {"type": "boolean", "description": "Raise the download budget to the hard cap for large pages/files. Use only after a result reported partial content."},
                    "query": {"type": "string", "description": "Optional comma-separated terms used to select matching passages/pages from long documents or PDFs, for example 'DocVQA, ChartQA, TextVQA, Qwen2.5-VL-72B'."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_extract",
            "description": "Extract focused evidence from an online PDF URL or task-local /workspace/*.pdf using Odysseus' native document reader. Use this instead of Python requests, curl, pdftotext, or manual downloads. Supply the exact model, metric, and table terms in query. Treat only values visible in the returned passages as verified; never fill missing values from memory or estimates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Direct http/https PDF URL or task-local /workspace/*.pdf path"},
                    "path": {"type": "string", "description": "Task-local /workspace/*.pdf path; use url for an online PDF"},
                    "query": {"type": "string", "description": "Required focused terms, including the target model and every requested metric/table heading"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "private_browser",
            "description": "Trusted browser session metadata only. Page/document operations are unavailable because the local producer cannot atomically bind a captured target. No batch or raw CLI flags. Use web_search/web_fetch for supported web access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["session_info", "tabs", "open", "read", "snapshot", "find", "evaluate", "click", "fill", "press", "scroll", "wait", "screenshot", "close", "navigate", "reload", "back", "forward", "select_page", "close_page", "network", "console", "new_page"]},
                    "page": {"type": "string", "pattern": "^t[1-9][0-9]*$", "description": "Observed alias only; page commands remain disabled for the current producer."},
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "target": {"type": "string"},
                    "ref": {"type": "string"},
                    "key": {"type": "string"},
                    "direction": {"type": "string"},
                    "text": {"type": "string"},
                    "value": {"type": "string"},
                    "script": {"type": "string"},
                    "path": {"type": "string"},
                    "find": {"type": "string"},
                    "amount": {"type": "integer"},
                    "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 20000}
                },
                "required": ["action"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_media",
            "description": "Inspect a local image, SVG, video, or PDF with the current multimodal model. For SVG, provide a .png output_path to render it reliably. For video, returns 4 timestamped 512px-max preview frames by default (up to 8 when needed) and can export clips or captioned still images. For PDF, renders query-relevant pages or an explicit page range as images for visual figure/table inspection. Use a focused start/end range for a known interval; increase frames only when sparse sampling could miss the requested event. This tool samples pixels; query/caption text does not perform semantic video search or event counting.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "description": "Workspace path to an image, SVG, video, or PDF, for example /workspace/fixtures/video.mp4"},
                    "start": {"type": "string", "description": "Optional range start in seconds, HH:MM:SS, or percent of video such as 25%"},
                    "end": {"type": "string", "description": "Optional range end in seconds, HH:MM:SS, or percent of video such as 75%"},
                    "duration": {"type": "string", "description": "Optional range length from start, in seconds or a compact duration such as 2m30s. Prefer explicit start/end when exact absolute boundaries matter; ignored when end is supplied."},
                    "frames": {"type": "integer", "minimum": 1, "maximum": 64, "description": "Number of video observations. Requests up to 8 return individual frames; denser requests up to 64 preserve the chosen strategy and are packed into bounded timestamped contact sheets. A uniform request above 8 is normalized to overview."},
                    "sampling": {"type": "string", "enum": ["uniform", "scene", "motion", "overview"], "description": "Video frame strategy. uniform covers the timeline evenly; scene finds visual cuts; motion ranks visually active moments; overview packs uniform observations into timestamped row-major contact sheets for coarse navigation. Any strategy requesting more than 8 observations uses contact-sheet transport. Refine candidate intervals afterward for event verification."},
                    "max_dimension": {"type": "integer", "minimum": 256, "maximum": 1024, "description": "Optional maximum width/height of transient preview images sent to the model; default 512. Increase to 768 or 1024 only for small visual details. Saved exports keep source quality."},
                    "query": {"type": "string", "description": "Label describing what to inspect in the returned visuals; it does not search for or filter video events"},
                    "page": {"type": "integer", "minimum": 1, "description": "For PDF only, optional first 1-based page to render"},
                    "pages": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 4}, {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}}], "description": "For PDF only, either the number of consecutive/relevant pages to return (default 2, maximum 4) or an ordered list of up to 12 exact 1-based page numbers"},
                    "timestamp": {"type": "string", "description": "Inspect one exact video frame, or select the frame for a still-image output_path; accepts seconds, HH:MM:SS, or percent"},
                    "output_path": {"type": "string", "description": "For SVG, the required PNG render path. For video, an optional clip or still path. The path must be inside /workspace; never use /tmp. To inspect and save every sampled video frame in one call, use an image template such as /workspace/frames/frame_{frame:02d}.png"},
                    "speed": {"type": "number", "minimum": 0.25, "maximum": 4.0, "description": "Optional playback-speed multiplier for an exported video clip, preserving audio pitch; for example 2 for 2x"},
                    "segments": {"type": "array", "maxItems": 32, "description": "Optional focused ranges. Without output_path, inspect up to 32 ranges: one midpoint per range by default, or distribute an explicit frames budget across them. With a video output_path, concatenate at most 12 ranges in listed order and preview their source midpoints. Positions accept seconds, HH:MM:SS, or percentages.", "items": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]}},
                    "exports": {"type": "array", "maxItems": 12, "description": "Export several timestamped video stills, or one video clip with start, end, and output_path. Every output_path must be inside /workspace; never use /tmp.", "items": {"type": "object", "properties": {"timestamp": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "output_path": {"type": "string", "description": "Required destination inside /workspace, for example /workspace/student1.png or /workspace/clip.mp4; never use /tmp"}, "speed": {"type": "number", "minimum": 0.25, "maximum": 4.0}, "timestamp_path": {"type": "string"}, "caption": {"type": "string", "description": "Visible text requested by the user; omit for evidence or ordinary extracted frames"}, "crop": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "width": {"type": "integer", "minimum": 1}, "height": {"type": "integer", "minimum": 1}}}}, "oneOf": [{"required": ["timestamp", "output_path"]}, {"required": ["start", "end", "output_path"]}]}},
                    "caption": {"type": "string", "description": "Optional text drawn on a saved still only when the user explicitly requested visible caption text. Never add a caption to internal evidence frames or use caption text as evidence about what the pixels show."},
                    "crop": {"type": "object", "description": "Optional still-image pixel crop", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "width": {"type": "integer", "minimum": 1}, "height": {"type": "integer", "minimum": 1}}},
                    "timestamp_path": {"type": "string", "description": "Optional workspace text file to save the exported clip's HH:MM:SS - HH:MM:SS range; use with output_path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_text",
            "description": "Extract exact visible text and pixel coordinates from an owned uploaded image or local workspace image with Odysseus local OCR. Use for screenshots, scans, labels, numbers, receipts, forms, and text-location tasks; use inspect_media for general visual understanding.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "description": "Supplied odysseus://attachment/ID reference for an owned upload, or a confined workspace image path."},
                    "mode": {"type": "string", "enum": ["all", "numbers"]},
                    "include_layout": {"type": "boolean", "description": "Include full bounding quadrilaterals; compact pixel centers are always returned."},
                    "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 512}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_media",
            "description": "Transcribe speech from a local workspace audio or video file with Odysseus local Whisper. Returns timestamped speech segments in [START --> END] TEXT form; the spoken text begins after the first '] '. For a request limited to a named chapter, question, scene, or topic, first locate that section and its next boundary, then use start/end or filter only that interval rather than matching the whole recording. This transcribes audio speech; use inspect_media for visually burned-in text or subtitles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace audio/video path, for example /workspace/fixtures/video.mp4"},
                    "language": {"type": "string", "description": "Optional speech-language hint such as zh or en; auto-detection remains the default."},
                    "force_language": {"type": "boolean", "description": "Set true only when the spoken language is known and auto-detection must be overridden."},
                    "start": {"type": "string", "description": "Optional transcription start in seconds or HH:MM:SS. Pass the requested start directly when the user names a time interval."},
                    "end": {"type": "string", "description": "Optional transcription end in seconds or HH:MM:SS. Pass the requested end directly instead of transcribing and filtering the whole recording."},
                    "model": {"type": "string", "enum": ["tiny", "base", "small"], "description": "Optional local Whisper size. Focused ranges up to five minutes default to small for accuracy; longer or unbounded recordings default to base for speed."},
                    "output_path": {"type": "string", "description": "Optional workspace destination for the timestamped transcript. Use .txt for readable timestamped text, .jsonl for one {start, end, text} object per speech segment, or .srt/.vtt for subtitle files. If the requested final artifact is one of these formats, pass its path directly so the tool writes it atomically. If omitted, a .txt transcript is saved automatically under /workspace/.odysseus/transcripts/."},
                    "timestamp_precision": {"type": "integer", "minimum": 0, "maximum": 3, "description": "Decimal places for JSONL start/end seconds (default 3). Match an explicitly requested rounding precision."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_tool",
            "description": "Read YouTube-specific data without fighting the JS UI. Use for YouTube video comments, transcripts, metadata, or latest video from a channel. For general web lookup use web_search; for interacting with the visible site use private_browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["comments", "transcript", "metadata", "latest_channel_video"], "description": "What YouTube data to retrieve. Use latest_channel_video with max_results for latest N uploads from a channel."},
                    "url": {"type": "string", "description": "YouTube video or channel URL"},
                    "video_url": {"type": "string", "description": "YouTube video URL"},
                    "video_id": {"type": "string", "description": "YouTube video id"},
                    "channel_url": {"type": "string", "description": "YouTube channel URL or /@handle/videos URL"},
                    "handle": {"type": "string", "description": "YouTube handle, e.g. @OpenAI"},
                    "max_results": {"type": "integer", "description": "Maximum comments, uploads, or results to return, capped at 50"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a local file from disk. This does not download URLs; use web_fetch for http:// or https:// resources. Optionally read a line range with offset/limit for large local files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "1-based line to start reading from (optional)"},
                    "limit": {"type": "integer", "description": "Max number of lines to read from offset (optional)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regular expression across a directory tree (uses ripgrep when available, respecting .gitignore). Returns file:line:match. PREFER this over `bash grep/rg` for code search — confined to the allowed roots, structured output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for"},
                    "path": {"type": "string", "description": "Directory or file to search (optional; defaults to the project root)"},
                    "glob": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py' (optional)"},
                    "ignore_case": {"type": "boolean", "description": "Case-insensitive match (optional)"},
                    "max_results": {"type": "integer", "description": "Max matches to return (optional)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern (recursive), newest first. e.g. '**/*.py'. PREFER this over `bash find/ls` for locating files — confined to the allowed roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.ts' or 'src/**/test_*.py'"},
                    "path": {"type": "string", "description": "Base directory (optional; defaults to the project root)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List the entries of a directory (folders first, then files with sizes). PREFER this over `bash ls` — confined to the allowed roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list (optional; defaults to the project root)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_workspace",
            "description": "Return the absolute path of the active workspace folder the user is working in. File tools are confined to it; the shell starts there but is not sandboxed. Call this first when the user refers to 'the project'/'the code'/'this folder' without a path, instead of asking them. Takes no arguments.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write/save one file to disk; when several independent files are required, "
                "issue multiple write_file calls in the same response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to. For SVG content, use a .svg path; render it to .png/.jpg with inspect_media instead of writing SVG XML under a raster extension."},
                    "content": {"type": "string", "description": "Complete file content to write. Keep generated artifacts within the response budget; prefer loops, reusable functions, CSS, and data arrays over repetitive literal markup."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file ON DISK by exact string replacement (home folder, project files, any real path like ~/sweden.txt or /path/to/file). This is the right tool for files on disk — NOT edit_document (that's for editor-panel documents). PREFER this over bash (sed/echo) — it shows a diff. old_string must match the file exactly and be unique (or set replace_all). Use write_file to create a new file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "old_string": {"type": "string", "description": "Exact text to replace (must match the file, including indentation)"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of requiring a unique match"}
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a multi-file source-code patch to disk. Use for real project files in the workspace when several edits belong together. Patch must use *** Begin Patch / *** End Patch with Add File, Update File, or Delete File sections. Prefer this over bash redirects/heredocs/sed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch_text": {
                        "type": "string",
                        "description": "Patch text beginning with *** Begin Patch and ending with *** End Patch"
                    }
                },
                "required": ["patch_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todowrite",
            "description": "Create and maintain a structured task list for the current coding session. Use during multi-step implementation/debug/refactor work and keep statuses current.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Current task list. Only one item should be in_progress.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "Task description"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                "priority": {"type": "string", "enum": ["low", "medium", "high"]}
                            },
                            "required": ["content", "status"]
                        }
                    }
                },
                "required": ["todos"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a new document in the editor panel. Use this when the user asks to write, create, build, make, or generate code, scripts, programs, games, apps, or any long-form or structured content that is more than a short paragraph, AND there is no already-open document/email draft that the request refers to. If an email compose draft is open, edit that draft instead of creating another document. NEVER put large generated content directly in chat — use this tool instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title"},
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript", "html", "css", "richtext", "markdown", "json", "yaml", "bash", "sql", "rust", "go", "java", "c", "cpp", "xml", "toml", "ini", "ruby", "php", "csv", "email", "text", "plain", "svg"],
                        "description": "Editor language or format. This is not a human-language code: use richtext for formatted prose/articles and markdown or text for plain prose; use html only for requested HTML source or a runnable page."
                    },
                    "content": {"type": "string", "description": "The document content"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_document",
            "description": "Edit a document OPEN IN THE EDITOR PANEL (created via create_document) — NOT a file on disk. For files on disk (home folder, project files, anything with a path like ~/x.txt or /path/to/file) use edit_file instead. Targeted find-and-replace with multiple FIND/REPLACE pairs per call; use for any edit smaller than a full rewrite. Do NOT send the whole file back via update_document for small edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "FIND/REPLACE command text using <<<FIND>>>...<<<REPLACE>>>...<<<END>>> blocks"
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of exact edits. Each target must be unique unless replace_all is explicitly true.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Exact text to find in the document"},
                                "replace": {"type": "string", "description": "Text to replace it with"},
                                "replace_all": {"type": "boolean", "description": "Set true to correct every exact occurrence of the same error throughout the document. Never use for selection-only edits."}
                            },
                            "required": ["find", "replace"]
                        }
                    },
                    "more": {"type": "boolean", "description": "Set true when more affected passages remain for a following edit batch."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_document",
            "description": "Suggest improvements to the active document WITHOUT editing it. Creates inline comment bubbles the user can accept or reject. Use when the user asks for suggestions, review, improvements, or feedback. Every replacement must materially differ from its exact source text; never emit a no-op suggestion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestions": {
                        "type": "array",
                        "description": "List of suggested changes with reasons",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Exact text in the document to suggest changing"},
                                "replace": {"type": "string", "description": "Suggested replacement text; MUST be materially different from find"},
                                "reason": {"type": "string", "description": "Brief explanation of why this change helps"}
                            },
                            "required": ["find", "replace", "reason"]
                        }
                    },
                    "more": {"type": "boolean", "description": "Set true when more distinct affected passages remain for a following suggestion batch."}
                },
                "required": ["suggestions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_document",
            "description": "Replace the ENTIRE active document. ONLY use for genuine full rewrites (>50% of lines changed). For any smaller change, use edit_document — echoing back the whole file for small edits is wasteful.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Complete new document content"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_chats",
            "description": "Search the user's past session transcripts by keyword. Use when the user asks about previous chats, past conversations, or when direct transcript evidence is better than persistent memory. Returns matching sessions with clickable links and nearby context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword(s) to find in past conversations"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_with_model",
            "description": "Send a message to another AI model and get its response. Use for getting a second opinion, delegating subtasks, or AI-to-AI communication.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Model name (e.g. 'qwen3-32b') or model@endpoint_name"},
                    "message": {"type": "string", "description": "The message to send to the model"}
                },
                "required": ["model", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_session",
            "description": "Create a new chat for ongoing conversations with a specific model. (The UI calls these 'chats'; 'session' is the internal term.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the new chat"},
                    "model": {"type": "string", "description": "Model name or model@endpoint_name"}
                },
                "required": ["name", "model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_sessions",
            "description": "List the user's chats (the UI calls them 'chats') as clickable markdown links. Use this to enumerate chats before opening, renaming, archiving, or deleting them. When replying to the user, preserve the returned [title](#session-id) links; do not strip them into plain text. Optionally filter by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional keyword to filter chats by name"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_session",
            "description": "Send a new message to an existing live chat and get that chat model's response. Do not use this to retrieve, read, summarize, or inspect old chats; use search_chats or list_sessions for past chat evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The id of the chat to send the message to"},
                    "message": {"type": "string", "description": "The message to send"}
                },
                "required": ["session_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pipeline",
            "description": "Run a multi-step AI pipeline where each model's output feeds the next. Example: Draft -> Critique -> Revise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Pipeline steps in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "model": {"type": "string", "description": "Model name for this step"},
                                "instruction": {"type": "string", "description": "What this step should do"}
                            },
                            "required": ["model", "instruction"]
                        }
                    }
                },
                "required": ["steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_session",
            "description": "Manage a chat: rename, archive, unarchive, delete, mark important, truncate history, or fork it. (The UI calls these 'chats'; 'session' is the internal term.) For destructive actions like delete, call list_sessions first and pass the exact id returned there; never invent ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["rename", "archive", "unarchive", "delete", "important", "unimportant", "truncate", "fork"],
                               "description": "The action to perform"},
                    "session_id": {"type": "string", "description": "Exact target chat id from list_sessions, or 'current' for the active chat where supported"},
                    "value": {"type": "string", "description": "Action parameter: new name (rename), keep_count (truncate/fork)"}
                },
                "required": ["action", "session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": "Manage the user's memory system: list, add, edit, delete, or search memories. Memories persist across sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "edit", "delete", "search"],
                               "description": "The action to perform"},
                    "command": {"type": "string", "description": "Text command form, e.g. add\\n<memory text>\\n<category>"},
                    "text": {"type": "string", "description": "Memory text (for add/edit) or search query (for search)"},
                    "memory_id": {"type": "string", "description": "Memory ID (for edit/delete)"},
                    "category": {"type": "string", "enum": ["fact", "event", "contact", "preference"],
                                 "description": "Memory category (for add/list filter)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": "List AI models across configured endpoints. A normal filter matches model IDs. Use filter='recommended' to detect this machine's GPU/VRAM/RAM/CPU and return ranked compatible models.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional keyword to filter models"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_control",
            "description": "Control the user interface. Actions: toggle (turn tools on/off), open_panel (open a modal: documents/library, gallery, calendar/schedule, email, sessions, notes, memories/brain, skills, settings, theme, cookbook; calendar supports month/week/year/agenda plus a date; Cookbook supports models/download, launch/serve, active/running, dependencies, and settings views), open_email_reply (legacy UI-only reply opener; prefer email MCP draft_email_reply for assistant-written reply drafts so a normal document-backed email draft is created), set_mode, switch_model, set_theme (built-in presets: dark, light, midnight, cyberpunk, retrowave, forest, ocean, ume, terminal, organs, gpt, claude, cute, eclipse, porcelain, arcade, blueprint, monolith, yoyo), create_theme (CREATE any custom theme with a name + colors object — pick distinctive, evocative hex colors that match the requested aesthetic, NOT generic defaults. The theme auto-applies after creation), get_theme, and get_toggles. When a user asks for ANY theme not in the built-in preset list, ALWAYS use create_theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["toggle", "open_panel", "open_email_reply", "set_mode", "switch_model", "set_theme", "create_theme", "get_theme", "get_toggles"],
                               "description": "The UI action. Use set_theme for presets, create_theme to build a custom theme with any hex colors"},
                    "name": {"type": "string", "description": "For toggle: web, bash, research, incognito, document_editor (aliases: shell, search, deepresearch, documents). For open_panel: documents, gallery, calendar/schedule, email, sessions, notes, brain/memories, skills, settings, theme/themes, cookbook; models and serve are Cookbook-view aliases. For open_email_reply: email UID. For set_theme: a preset theme name. For create_theme: the custom theme name."},
                    "view": {"type": "string", "description": "Optional open_panel subview: calendar day/week/month/year/agenda, or Cookbook models/download, launch/serve, active/running, dependencies, settings."},
                    "value": {"type": "string", "description": "Value: on/off for toggle, agent/chat for set_mode, model name for switch_model, theme name for set_theme, or folder for open_email_reply"},
                    "uid": {"type": "string", "description": "Email UID for open_email_reply"},
                    "folder": {"type": "string", "description": "Email folder for open_email_reply (default INBOX)"},
                    "mode": {"type": "string", "description": "Reply draft mode for open_email_reply: reply, reply-all, or ai-reply"},
                    "body": {"type": "string", "description": "For open_email_reply: reply body to pre-fill. Required whenever the user told you what the reply should say. Opens a draft, does not send."},
                    "background": {"type": "object", "description": "For create_theme: choose an effect matching the requested mood. Use none for a plain background or random for a saved random choice.", "properties": {
                        "pattern": {"type": "string", "enum": ["none", "dots", "synapse", "rain", "constellations", "perlin-flow", "petals", "sparkles", "embers", "starfield-depth", "ascii-fireflies", "random"]},
                        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                        "size": {"type": "number", "minimum": 0.2, "maximum": 3},
                        "speed": {"type": "number", "minimum": 0.05, "maximum": 2.5}
                    }, "required": ["pattern"]},
                    "colors": {"type": "object", "description": "For create_theme: choose bg and accent. Omitted fg, panel and border are derived for readability. Accepts #RGB or #RRGGBB. Explicit overrides are preserved.",
                               "properties": {
                                   "bg": {"type": "string", "description": "Background color (hex, e.g. #1a1a2e)"},
                                   "fg": {"type": "string", "description": "Foreground/text color (hex)"},
                                   "panel": {"type": "string", "description": "Panel/sidebar background color (hex)"},
                                   "border": {"type": "string", "description": "Border/divider color (hex)"},
                                   "accent": {"type": "string", "description": "Accent color for buttons, brand, highlights (hex)"},
                                   "userBubbleBg": {"type": "string", "description": "User chat bubble background (hex, optional)"},
                                   "aiBubbleBg": {"type": "string", "description": "AI chat bubble background (hex, optional)"},
                                   "bubbleBorder": {"type": "string", "description": "Chat bubble border color (hex, optional)"},
                                   "sidebarBg": {"type": "string", "description": "Sidebar background override (hex, optional)"},
                                   "sectionAccent": {"type": "string", "description": "Section header accent color (hex, optional)"},
                                   "brandColor": {"type": "string", "description": "Brand/logo color (hex, optional)"},
                                   "inputBg": {"type": "string", "description": "Chat input background (hex, optional)"},
                                   "inputBorder": {"type": "string", "description": "Chat input border (hex, optional)"},
                                   "sendBtnBg": {"type": "string", "description": "Send button background (hex, optional)"},
                                   "sendBtnHover": {"type": "string", "description": "Send button hover color (hex, optional)"},
                                   "codeBg": {"type": "string", "description": "Code block background (hex, optional)"},
                                   "codeFg": {"type": "string", "description": "Code block text color (hex, optional)"},
                                   "toggleBg": {"type": "string", "description": "Toggle switch off background (hex, optional)"},
                                   "toggleActive": {"type": "string", "description": "Toggle switch on color (hex, optional)"},
                                   "accentPrimary": {"type": "string", "description": "Primary accent override (hex, optional)"},
                                   "accentError": {"type": "string", "description": "Error/danger color (hex, optional)"}
                               },
                               "required": ["bg", "accent"]}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a question to get a decision or clarification when the task is genuinely ambiguous and the answer changes what you do next (e.g. pick between approaches, confirm an assumption, choose a target, or request required missing data). The user sees clickable option buttons; calling this ENDS your turn and their selection arrives as your next message. For open-ended missing data such as an exact calendar date, include an 'Exact date' option and ask the user to type it; do not invent arbitrary choices. Prefer sensible defaults over asking — only ask when you truly cannot proceed well without the user's input. Do NOT use it to confirm irreversible/destructive actions that have a dedicated confirmation flow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask. Be specific and self-contained."},
                    "options": {
                        "type": "array",
                        "description": "2-6 choices. Each is an object with a short `label` and an optional `description` explaining the trade-off. For open-ended missing data, include a generic option such as 'Exact date' instead of guessing arbitrary values.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Concise choice text the user clicks (1-5 words)."},
                                "description": {"type": "string", "description": "Optional one-line explanation of this choice."}
                            },
                            "required": ["label"]
                        }
                    },
                    "multi": {"type": "boolean", "description": "Set true ONLY when the question explicitly allows choosing more than one option. Otherwise omit it or set false. Default false."}
                },
                "required": ["question", "options"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Write back to the ACTIVE PLAN: mark steps done or revise them. Use this while executing an approved plan — after you finish a step, call update_plan with the full checklist and that step marked `- [x]`; when the user asks to change the plan, call it with the revised checklist. The user's docked plan window updates live. Pass the COMPLETE checklist every time (not a diff). No effect if there is no active plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"type": "string", "description": "The full updated plan as a GitHub-style markdown checklist — one step per line, `- [ ]` for pending and `- [x]` for done. Always send the whole list."}
                },
                "required": ["plan"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "Manage scheduled/automated tasks: list, create, edit, delete, pause, resume, or run tasks. Use this for ANY recurring/scheduled request ('every morning…', 'each day at 7:30', 'daily summarize…') and for explicit one-off future retry requests ('try again in 15 minutes') — create a task rather than claiming the current agent can sleep. Task types: llm (AI runs a prompt), research (runs the deep-research pipeline on a question), or action (built-in automation). Triggers can be time-based or event-based.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "edit", "delete", "pause", "resume", "run"],
                               "description": "The action to perform"},
                    "task_id": {"type": "string", "description": "Task ID (for edit/delete/pause/resume/run)"},
                    "name": {"type": "string", "description": "Task name"},
                    "query": {"type": "string", "description": "Text filter for action=list when searching task names/prompts"},
                    "status": {"type": "string", "enum": ["active", "paused"], "description": "Optional status filter for action=list"},
                    "prompt": {"type": "string", "description": "The instruction (for task_type=llm) or the research question (for task_type=research). Required for both."},
                    "task_type": {"type": "string", "enum": ["llm", "research", "action"],
                                  "description": "llm = AI runs your prompt; research = runs the deep-research pipeline on the prompt as a question; action = direct built-in function"},
                    "action_name": {"type": "string", "enum": [
                        "tidy_sessions", "tidy_documents", "consolidate_memory", "tidy_research",
                        "summarize_emails", "draft_email_replies", "extract_email_events",
                        "classify_events", "learn_sender_signatures",
                        "test_skills", "audit_skills", "check_email_urgency"
                    ],
                                    "description": "Built-in action (for task_type=action)"},
                    "trigger_type": {"type": "string", "enum": ["schedule", "event"],
                                     "description": "schedule = time-based, event = count-based"},
                    "schedule": {"type": "string", "enum": ["once", "daily", "weekly", "monthly", "cron"],
                                 "description": "Schedule frequency (for trigger_type=schedule)"},
                    "cron_expression": {"type": "string", "description": "For schedule=cron: five-field UTC cron (minute hour day-of-month month weekday). Use for multiple weekdays or other custom recurrence; weekdays 0=Sunday, 1=Monday."},
                    "weekdays": {"type": "array", "minItems": 1, "uniqueItems": True,
                                 "items": {"type": "string", "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
                                 "description": "Days for one recurring task, with scheduled_time in UTC. Server builds the schedule; omit day_of_month, scheduled_date, cron_expression and scheduled_day."},
                    "scheduled_time": {"type": "string", "description": "HH:MM in UTC (for schedule triggers). Convert the user's stated local time using the UTC offset given in the 'Current date and time' context."},
                    "scheduled_day": {"type": "integer", "description": "Day of week 0=Mon (weekly) or day of month (monthly)"},
                    "day_of_month": {"type": "integer", "minimum": 1, "maximum": 31,
                                     "description": "Day of month for a monthly task. For weekly tasks use weekdays instead."},
                    "scheduled_date": {"type": "string", "description": "ISO datetime for one-off tasks when schedule is 'once', e.g. 2026-08-23T14:30:00Z."},
                    "trigger_event": {"type": "string", "enum": ["session_created", "message_sent", "document_created", "memory_added", "research_completed", "email_received", "skill_added"],
                                      "description": "Event name (for trigger_type=event)"},
                    "trigger_count": {"type": "integer", "description": "Fire every N events (for trigger_type=event)"},
                    "output_target": {"type": "string", "description": "Where results go. Defaults to 'session' (results land in a dedicated chat session the user reads) — this is the right choice for 'summarize for me' / 'send to me'. Do NOT go hunting for the user's email address; only use an email MCP tool name here if the user explicitly asked to be emailed AND an address is already known."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_calendar",
            "description": "Manage calendar events: list events in a date range, create, update, delete. Each event can carry a tag/category (event_type) and importance level. Resolve relative dates like today/tomorrow against the 'Current date and time' system context, then pass ISO 8601 datetimes in the user's local wall time; for all-day events set all_day=true and pass YYYY-MM-DD. If a create/update request lacks a required date, time, or target event, call ask_user once instead of guessing. For update_event, only pass event_type/tag/category/type when the user explicitly asks to tag, retag, categorize, or clear the event tag; otherwise omit it so manually tagged events keep their existing tag. For event reminders/alarms, pass reminder_minutes; the tool creates the Odysseus note reminder, so do not also call manage_notes for the same reminder. Do not set rrule for single-occurrence requests such as 'next Wednesday only'; use rrule only when the user explicitly wants recurrence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list_events", "create_event", "update_event", "delete_event", "list_calendars"],
                               "description": "Action to perform"},
                    "summary": {"type": "string", "description": "Event title (for create/update)"},
                    "local_start": {"type": "object", "description": "Original stated start date and clock time; backend handles timezone conversion. Alternative to dtstart.", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "HH:MM or HH:MM:SS; omit for all_day=true"}}, "required": ["date"]},
                    "local_end": {"type": "object", "description": "End date and clock time in the same timezone as local_start. Alternative to dtend.", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "HH:MM or HH:MM:SS; omit for all_day=true"}}, "required": ["date"]},
                    "timezone": {"type": "string", "description": "For timed create/update: stated timezone, e.g. UTC, +05:30, or Europe/Paris. Pass dtstart/dtend in that zone's original clock time; the backend converts. Omit for user-local time or all-day dates."},
                    "dtstart": {"type": "string", "description": "Start ISO datetime, or YYYY-MM-DD if all_day"},
                    "dtend": {"type": "string", "description": "End ISO datetime; defaults to +1h (or +1 day for all_day)"},
                    "all_day": {"type": "boolean", "description": "Whether this is an all-day event"},
                    "description": {"type": "string", "description": "Event description / notes"},
                    "location": {"type": "string", "description": "Event location"},
                    "uid": {"type": "string", "description": "Event UID (for update/delete)"},
                    "calendar_href": {"type": "string", "description": "Specific calendar URL (optional; defaults to first calendar)"},
                    "calendar": {"type": "string", "description": "Filter list_events by calendar name or href"},
                    "start": {"type": "string", "description": "list_events range start (ISO datetime). Use this for month/week requests after resolving the date range; pass start and end in the same call. Prefer start; backend also accepts start_time, start_date, range_start, from, dtstart, since."},
                    "end": {"type": "string", "description": "list_events range end (ISO datetime). Use this for month/week requests after resolving the date range; defaults to +14 days only when no range is requested. Prefer end; backend also accepts end_time, end_date, range_end, to, dtend, until."},
                    "query": {"type": "string", "description": "Optional text filter for list_events. Use with explicit start/end when verifying whether a named event is present or absent; do not pass query by itself."},
                    "event_type": {"type": "string", "description": "Tag / category for the event. Common values: work, personal, health, travel, meal, social, admin, other. Aliases accepted: tag, category, type. For update_event, omit this unless the user explicitly asks to tag, retag, categorize, or clear the tag; omitting preserves manual tags."},
                    "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"], "description": "Priority level (defaults to 'normal')"},
                    "reminder_minutes": {"type": "integer", "description": "For create_event/update_event: create or replace the Odysseus reminder this many minutes before the event, e.g. 5 for 'reminder 5 min before'. For update_event, use reminder='off' to remove the reminder."},
                    "rrule": {"type": "string", "description": "Recurrence rule in iCalendar RRULE format, e.g. 'FREQ=WEEKLY;BYDAY=MO' for weekly on Monday, 'FREQ=MONTHLY;BYMONTHDAY=1' for the first day of each month, 'FREQ=MONTHLY;BYDAY=1MO,-1MO' for the first and last Monday of each month, 'FREQ=MONTHLY;BYDAY=2TH' for the second Thursday of each month, or 'FREQ=MONTHLY;BYDAY=-1SU' for the last Sunday of each month. Use with create_event or update_event. For update_event, pass an explicit empty string to remove recurrence and make the event single-occurrence."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_notes",
            "description": "Saved notes/checklists only; no tools for word definitions like musical note or pinned; tagged/archive lists use action=list+label/archived; archive uses update. Use view only with an explicit note id when you need the full body. Search is only for saved-note title/topic lookup. IMPORTANT: For to-do lists / checklists, set note_type='checklist' and pass the items as the `checklist_items` array — do NOT serialize them into `content` as plain text. For freeform notes, use note_type='note' and put the body in `content`. `due_date` accepts natural language like 'tomorrow at 9am' (parsed in the user's timezone) and fires a notification — do not also create a calendar event for the same reminder. Delete only when the user explicitly says delete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "search", "view", "add", "update", "delete", "toggle_item"],
                               "description": "The action to perform"},
                    "id": {"type": "string", "description": "Note id (for view/update/delete/toggle_item); 8-char prefix is fine"},
                    "note_id": {"type": "string", "description": "Alias for id; accepted for view/update/delete/toggle_item when the model emits note_id"},
                    "query": {"type": "string", "description": "Search text for action='search'"},
                    "title": {"type": "string", "description": "Note title (for add/update, or exact-title delete when no id is available)"},
                    "content": {"type": "string", "description": "Freeform body text. Use this for note_type='note'. Do NOT use this for checklists — pass `checklist_items` instead."},
                    "note_type": {"type": "string", "enum": ["note", "checklist"],
                                  "description": "'note' = freeform text in `content`. 'checklist' = structured to-do items in `checklist_items`. Defaults to 'checklist' if checklist_items is supplied, else 'note'."},
                    "checklist_items": {"type": "array",
                                        "items": {"type": "object",
                                                  "properties": {
                                                      "text": {"type": "string", "description": "The to-do item text"},
                                                      "done": {"type": "boolean", "description": "Whether the item is checked off"}
                                                  },
                                                  "required": ["text"]},
                                        "description": "Checklist items for note_type='checklist'. Each item is {text, done}. REQUIRED for checklists — leaving this empty produces a blank note."},
                    "color": {"type": "string", "description": "Optional color label (e.g. 'yellow', 'blue', 'green')"},
                    "label": {"type": "string", "description": "Optional category label (also used as a list filter)"},
                    "pinned": {"type": "boolean", "description": "Pin the note to the top"},
                    "archived": {"type": "boolean", "description": "For update: archive/unarchive. For list: show archived notes when true."},
                    "due_date": {"type": "string", "description": "Reminder time. Accepts natural language ('tomorrow at 9am', '11pm today') or ISO 8601. Fires a notification at that time."},
                    "index": {"type": "integer", "description": "Required for toggle_item: 0-based checklist item index. Use view if unknown."},
                    "done": {"type": "boolean", "description": "For toggle_item: target checked state; omit to toggle."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "api_call",
            "description": "Call a registered API integration (RSS reader, git forge, bookmark manager, smart home, etc.). Check the system context for available integrations and their endpoints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "integration": {"type": "string", "description": "Integration name or ID (e.g. 'Miniflux', 'Gitea')"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method"},
                    "path": {"type": "string", "description": "API endpoint path (e.g. '/v1/entries?status=unread&limit=20')"},
                    "body": {"type": "object", "description": "JSON request body (for POST/PUT/PATCH)"}
                },
                "required": ["integration", "method", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_teacher",
            "description": "Ask a more capable AI model for help when stuck on a difficult problem. The teacher provides guidance that can be saved as a learned skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Teacher model name (e.g. 'claude-sonnet-4') or 'auto'. Ignored when a teacher model is configured in settings."},
                    "problem": {"type": "string", "description": "Describe the problem or question you need help with"}
                },
                "required": ["problem"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_skills",
            "description": (
                "Read or modify the user's skill library. Skills are SKILL.md files "
                "(YAML frontmatter + structured body: When to Use / Procedure / "
                "Pitfalls / Verification) and follow a draft → published lifecycle. "
                "Use progressive disclosure: 'list' to see what exists, 'view' to "
                "load full content for a single skill, 'view_ref' for sub-files. "
                "Use 'patch' for surgical text edits and 'edit' for full rewrites. "
                "'publish' once you've verified the procedure works. For add, "
                "always provide an explicit name slug and only tell the user the "
                "exact name returned by the tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "view", "view_ref", "add", "edit", "patch", "publish", "delete", "search"], "description": "list = name+description summary; view = full SKILL.md; view_ref = sub-file under the skill dir; add = create; edit = full rewrite (content); patch = old_string→new_string; publish = flip status; delete; search = relevance match on published skills."},
                    "name": {"type": "string", "description": "Slug/name of the skill. Required for add/view/view_ref/edit/patch/publish/delete. For add, choose the exact kebab-case name the user should see and report only the returned name."},
                    "path": {"type": "string", "description": "Sub-path under the skill directory for view_ref (e.g. 'references/example.md')."},
                    "description": {"type": "string", "description": "One-line summary surfaced in the skills index (for add or metadata-only edit)."},
                    "category": {"type": "string", "description": "Organizational grouping like 'dev', 'email', 'system' (for add)."},
                    "when_to_use": {"type": "string", "description": "Trigger conditions in plain English (for add)."},
                    "procedure": {"type": "array", "items": {"type": "string"}, "description": "Numbered steps (for add)."},
                    "pitfalls": {"type": "array", "items": {"type": "string"}, "description": "Known failure modes + recovery (for add)."},
                    "verification": {"type": "array", "items": {"type": "string"}, "description": "How to confirm the procedure succeeded (for add)."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Keyword tags (for add)."},
                    "platforms": {"type": "array", "items": {"type": "string"}, "description": "Restrict to OSes (for add)."},
                    "requires_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide unless these toolsets are active (for add)."},
                    "fallback_for_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide when these toolsets are active (for add)."},
                    "status": {"type": "string", "enum": ["draft", "published"], "description": "Defaults to 'draft' on add."},
                    "version": {"type": "string", "description": "Semver-ish, e.g. '1.0.0' (for add)."},
                    "confidence": {"type": "number", "description": "0-1 (for add/publish)."},
                    "content": {"type": "string", "description": "Full SKILL.md text (for edit)."},
                    "old_string": {"type": "string", "description": "Exact substring to replace (for patch). Must appear exactly once."},
                    "new_string": {"type": "string", "description": "Replacement text (for patch)."},
                    "query": {"type": "string", "description": "Search query (for search)."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_endpoints",
            "description": "Manage model API endpoints: list configured endpoints, add new ones, delete, enable or disable them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable"]},
                    "endpoint_id": {"type": "string", "description": "Endpoint ID (for delete/enable/disable)"},
                    "name": {"type": "string", "description": "Display name (for add)"},
                    "base_url": {"type": "string", "description": "API base URL e.g. https://api.openai.com/v1 (for add)"},
                    "api_key": {"type": "string", "description": "API key (for add)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_mcp",
            "description": "Manage MCP (Model Context Protocol) tool servers: list servers and their tools, add new servers, delete, enable/disable, reconnect, or list all available tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable", "reconnect", "list_tools"]},
                    "server_id": {"type": "string", "description": "Server ID (for delete/enable/disable/reconnect)"},
                    "name": {"type": "string", "description": "Server name (for add)"},
                    "command": {"type": "string", "description": "Command to run e.g. npx (for add)"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments (for add)"},
                    "env": {"type": "object", "description": "Environment variables (for add)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_webhooks",
            "description": "Manage webhooks: list, add, delete, enable or disable webhook endpoints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable"]},
                    "webhook_id": {"type": "string", "description": "Webhook ID (for delete/enable/disable)"},
                    "name": {"type": "string", "description": "Webhook name (for add)"},
                    "url": {"type": "string", "description": "Webhook URL (for add)"},
                    "events": {"type": "string", "description": "Comma-separated event names (for add)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tokens",
            "description": "Manage API access tokens: list existing tokens, create new ones, or delete them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "delete"]},
                    "token_id": {"type": "string", "description": "Token ID (for delete)"},
                    "name": {"type": "string", "description": "Token name (for create)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_documents",
            "description": "Manage stored editor-panel documents. Use this for ANY request to find/search/list/open/show/read/summarize/quote a document by title or content. For a titled document, call action='list' with search/title text to get the document_id, then action='read' with that document_id before answering. Do not answer document-read requests from memory or injected context when this tool is available. Also supports delete and tidy cleanup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "read", "delete", "tidy"]},
                    "document_id": {"type": "string", "description": "Document ID (for read/delete)"},
                    "search": {"type": "string", "description": "Search query (for list)"},
                    "language": {"type": "string", "description": "Filter by language (for list)"},
                    "limit": {"type": "integer", "description": "Max results for list or max characters for read"},
                    "offset": {"type": "integer", "description": "Character offset for read pagination"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_settings",
            "description": "Manage user preferences and settings. Use `disable_tool`/`enable_tool`/`list_tools` to turn individual tools on or off globally (e.g. shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email). Use list/get/set/delete for free-form preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "set", "delete", "disable_tool", "enable_tool", "list_tools"]},
                    "key": {"type": "string", "description": "Setting key (for get/set/delete)"},
                    "value": {"description": "Setting value (for set) — can be string, number, boolean, or object"},
                    "tool": {"type": "string", "description": "Tool name to disable/enable (for disable_tool/enable_tool). Accepts aliases: shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email — or a raw tool name like 'bash' or 'web_search'."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_model",
            "description": "Download a HuggingFace model to a server. If `host` is omitted, defaults to the cookbook's currently-selected server (NOT localhost) — call list_cookbook_servers first if you're unsure where it should go.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "HuggingFace repo (e.g. 'Qwen/Qwen3-8B')"},
                    "host": {"type": "string", "description": "Target server — use the friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or a raw user@host. Omit to use the cookbook's selected default server."},
                    "local": {"type": "boolean", "description": "Force download to THIS machine (localhost) instead of the default remote server."},
                    "include": {"type": "string", "description": "Glob filter for specific files (e.g. '*Q4_K_M*')"},
                },
                "required": ["repo_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_model",
            "description": "Start serving a model with vLLM, SGLang, llama.cpp, Ollama, MLX Image, or Diffusers. If `host` is omitted, defaults to the cookbook's selected server (not localhost). For MLX image models on Apple Silicon use `python3 scripts/mlx_image_server.py --model <repo> --port 8100`; for non-MLX image/inpainting/diffusion models use `python3 scripts/diffusion_server.py --model <repo> --port 8100`. Never serve image models with `mlx_lm.server`; that is only for text/chat MLX models. After launching, call list_served_models to check readiness/errors; if it reports a diagnosis with retry suggestions, retry via serve_model using the suggested adjusted cmd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Model repo (e.g. 'Qwen/Qwen3-8B')"},
                    "cmd": {"type": "string", "description": "Full serve command (e.g. 'vllm serve <repo> --port 8000 --tp 2', 'python3 -m sglang.launch_server --model-path <repo> --port 30000', for MLX image models: 'python3 scripts/mlx_image_server.py --model <repo> --port 8100', or for non-MLX image models: 'python3 scripts/diffusion_server.py --model <repo> --port 8100')"},
                    "host": {"type": "string", "description": "Target server — friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or raw user@host. Omit to use the cookbook's selected default."},
                    "local": {"type": "boolean", "description": "Force serve on THIS machine instead of the default remote server."},
                },
                "required": ["repo_id", "cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_served_models",
            "description": "List currently running model servers with status, model name, port, throughput, and structured Cookbook diagnoses. If a serve failed, this includes recent logs plus retry suggestions/adjusted commands the agent can use with serve_model.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_served_model",
            "description": "Stop a running model server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID of the server to stop"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tail_serve_output",
            "description": "Read the last N lines of a cookbook serve/download task's tmux pane. Use ONLY in this exact sequence: (1) the user asked to serve a model, (2) you launched it via serve_model, (3) list_served_models reports the NEW task as crashed/error, (4) call tail_serve_output on the new sessionId to find the root cause, (5) call serve_model again with adjusted flags. DO NOT call this on old stopped/completed download tasks — they are historical and won't tell you anything about the current attempt. DO NOT investigate past failures before launching; the environment may have changed since.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session id from list_served_models (e.g. 'serve-abc12345', 'cookbook-a1b2c3d4')."},
                    "tail": {"type": "integer", "description": "How many lines of pane scrollback to fetch (default 300, max 4000). Bump this if the error in the visible tail references an earlier line ('see root cause above')."},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_downloads",
            "description": "List in-progress model downloads in the Cookbook. Shows each download's model name, phase, percent (if available), session ID, and remote host.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_download",
            "description": "Cancel an in-progress model download by killing its tmux session. Use list_downloads first to get the session_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID from list_downloads (e.g. 'cookbook-a1b2c3d4')"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_hf_models",
            "description": "Search Hugging Face Hub models using the official HF API. Returns repo IDs, HF URLs, update times, likes, and download counts. Use this when the user wants to find/link a model to download. Set official_only=true for 'official', 'latest Qwen/DeepSeek/Llama/etc.', or provider-owned models. Do not include quant/community variants by default; only search AWQ/GGUF/GPTQ/FP8/Q4/etc. when the user asks for quantized versions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms (e.g. 'Qwen 8B', 'flux', 'llama-3 instruct')"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                    "official_only": {"type": "boolean", "description": "Restrict to the official provider/org namespace inferred from the query, such as Qwen, deepseek-ai, meta-llama, mistralai, google, microsoft, nvidia, or openai."},
                    "author": {"type": "string", "description": "Optional exact Hugging Face namespace/author to search, e.g. Qwen or meta-llama."},
                    "quant": {"type": "boolean", "description": "Set true only when searching quantized variants such as AWQ, GGUF, GPTQ, EXL2, MLX, FP8, Q4, or Q8."},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cookbook_servers",
            "description": "List the cookbook's configured servers (remote GPU boxes + local) and the current default host. Call this before download_model/serve_model when the user didn't specify a host, so models go to the right machine (where the GPUs and model cache are) instead of localhost. If multiple servers and intent is ambiguous, show them and ask the user which.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_serve_presets",
            "description": "List saved Cookbook serve presets. Each preset is a launch template (name, model, host, port, tmux cmd) the user previously saved from the UI. Call this BEFORE raw serve_model when the user asks to launch a model by name manually.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adopt_served_model",
            "description": "Register an existing tmux model server (started manually or outside the cookbook flow) into Cookbook tracking, AND add it as a chat endpoint. Use when the user (or you) launched something via ssh+tmux and now want it visible in the UI / stoppable via stop_served_model / usable in the model picker. Verifies the tmux session + port respond before adding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Remote host in user@host form (e.g. 'user@192.0.2.10'). Omit for localhost."},
                    "tmux_session": {"type": "string", "description": "Existing tmux session name (e.g. 'minimax-m27')"},
                    "model": {"type": "string", "description": "Model repo_id or display name (e.g. 'cyankiwi/MiniMax-M2.7-AWQ-4bit')"},
                    "port": {"type": "integer", "description": "Port the server is listening on (default 8000)"},
                    "name": {"type": "string", "description": "Optional display name (defaults to model basename)"},
                    "add_endpoint": {"type": "boolean", "description": "Also register as a chat endpoint (default true)"},
                    "dry_run": {"type": "boolean", "description": "Preview validation and registration without checking tmux or changing state"}
                },
                "required": ["tmux_session", "model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_preset",
            "description": "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user saved before. This is the preferred way to start a known model (SD3.5, vLLM presets, etc.) — don't fabricate launch commands when a preset exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name (exact or case-insensitive substring of one returned by list_serve_presets)"},
                    "dry_run": {"type": "boolean", "description": "Resolve and preview the preset without starting a server"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cached_models",
            "description": "List models already cached on disk locally or on a remote server. `host` accepts friendly Cookbook server names from list_cookbook_servers (for example workstation) or raw user@host. Also reports completed Cookbook download tasks when the filesystem cache scan cannot locate the HF cache path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Friendly Cookbook server name (e.g. 'workstation', 'gpu-box') or raw remote host (e.g. 'user@gpu-box'). Omit for local."},
                    "model_dir": {"type": "string", "description": "Comma-separated additional model directories to scan beyond ~/.cache/huggingface/hub"},
                    "ssh_port": {"type": "string", "description": "SSH port for remote host (default 22)"},
                    "platform": {"type": "string", "enum": ["linux", "windows"], "description": "Remote platform"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "app_api",
            "description": "Generic loopback to allowed internal Odysseus endpoints. Use this when there's no named tool for what the user wants. For 'best model for my hardware', call GET /api/hwfit/models with query {fit_only:true,limit:10,sort:'fit'}; it detects GPU/VRAM/RAM/CPU and returns ranked compatible models. Hits the same routes the UI buttons hit (cookbook, gallery, library/documents, memory, notes, calendar, tasks, settings, themes, research, compare, etc.). action='endpoints' returns the OpenAPI surface (use `filter` to narrow). action='call' (default) takes method+path+body. Sensitive auth/user/admin/shell paths and host-control Cookbook mutation routes are blocked for safety. Do not use for shell commands; use named command tooling instead. Do not use for package installs, engine rebuilds, PID signalling, or email account discovery; use list_email_accounts for email accounts because /api/email/accounts is owner-filtered in tool context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["call", "endpoints"], "description": "'call' to hit an endpoint, 'endpoints' to list what's available"},
                    "path": {"type": "string", "description": "Endpoint path starting with /api/ (e.g. '/api/cookbook/gpus', '/api/gallery/list', '/api/calendar/events')"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method (default GET)"},
                    "body": {"type": "object", "description": "JSON request body for POST/PUT/PATCH"},
                    "query": {"type": "object", "description": "Querystring params as a key-value object"},
                    "filter": {"type": "string", "description": "For action=endpoints: substring to filter paths/summaries (e.g. 'cookbook', 'gallery')"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an AI image from a text prompt and save it to the gallery. If this tool reports that no image model or endpoint is configured, report that limitation directly; do not substitute Bash, Python, SVG, or another tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed image description"},
                    "model": {"type": "string", "description": "Optional image model"},
                    "size": {"type": "string", "description": "Optional output size"},
                    "quality": {"type": "string", "description": "Optional output quality"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "Edit an existing gallery image, preserving it as the source. For follow-ups such as adding an object or changing colors, use action=prompt with the previous tool result's image_id and the edit instructions. This sends the actual image plus prompt to the configured image model and saves a new copy. Also supports upscale and rembg. Report a missing optional dependency or unavailable editing directly; do not install packages or substitute a new text-only generation or shell commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string", "description": "Gallery image ID or supplied odysseus://attachment/ID reference for an owned upload"},
                    "action": {"type": "string", "enum": ["prompt", "upscale", "rembg"], "description": "Edit action"},
                    "prompt": {"type": "string", "description": "For action=prompt: requested changes, preserving the rest of the source image"},
                    "scale": {"type": "number", "description": "For upscale: scale factor (default 2)"},
                },
                "required": ["image_id", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_research",
            "description": "Start background research on a topic. Chat-started jobs return findings to this chat. Chat defaults to a quick two-round pass; set max_rounds explicitly for deeper work. Returns a progress link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Research question or topic"},
                    "category": {"type": "string", "enum": ["product", "comparison", "howto", "factcheck"], "description": "Optional report format."},
                    "max_rounds": {"type": "integer", "minimum": 0, "description": "Optional research-round limit; omit or use 0 for automatic rounds."},
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_research",
            "description": "List, read/open, or delete saved deep research reports from the Library. Use this for existing research reports; use trigger_research to start a new report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "read", "open", "view", "get", "delete"], "description": "List reports, read/open one report, or delete one report."},
                    "id": {"type": "string", "description": "Research report id from action=list, required for read/open/view/get/delete."},
                    "search": {"type": "string", "description": "Optional search text for action=list."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_contact",
            "description": "Look up a contact by name. Searches CardDAV address book and sent email history. Returns email addresses (when available) or phone numbers. Use when the user says 'message [name]', 'email [name]', or asks for someone's contact details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name to search for"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_contact",
            "description": "Create, update, delete, or list the user's CardDAV contacts. Use to save a new contact, update an existing one (email/phone/address), or remove one. Add does not require email: name + phone or name + address is valid. For update/delete you need the contact's uid — call action='list' first to find it. Writes go through the same dedupe + validation as the Contacts UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "search", "find", "add", "update", "delete"],
                               "description": "list = show all contacts (with uids); search/find = filter contacts by name/email/phone; add = create; update = edit by uid; delete = remove by uid."},
                    "uid": {"type": "string", "description": "Contact UID (required for update/delete; get it from action=list)."},
                    "name": {"type": "string", "description": "Contact's display name (for add/update)."},
                    "email": {"type": "string", "description": "Single email address (convenience for add, or the primary email for update). Optional when phone or address is provided."},
                    "emails": {"type": "array", "items": {"type": "string"}, "description": "Full list of email addresses (first is primary)."},
                    "phones": {"type": "array", "items": {"type": "string"}, "description": "Full list of phone numbers. Valid for add/update."},
                    "address": {"type": "string", "description": "Postal/mailing address as a single human-readable string."},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_accounts",
            "description": "List configured email accounts. Use this before checking mail when the user names a mailbox/account such as Gmail, work, or a custom domain, then pass the returned account name/email/id to the other email tools.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send a new email immediately. Use only when the user explicitly says to send now, deliver now, or approve/send without further review. For normal 'send/write/email someone saying X' requests, use draft_email so Odysseus opens a reviewable email document. Use resolve_contact first if you only have a name and need to find the email address. If multiple accounts exist, pass account from list_email_accounts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body text"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email",
            "description": "Create a new Odysseus email draft document for review. This does not send. Use this as the default for normal 'send/write/email someone saying X' requests so the user sees the composed message in the document editor and can edit/send it from there.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Draft body text"},
                    "cc": {"type": "string", "description": "Optional CC recipients"},
                    "bcc": {"type": "string", "description": "Optional BCC recipients"},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email_reply",
            "description": "Create a threaded Odysseus reply draft document by email UID. This does not send. Use this for normal 'reply/write back/send them X' requests so the user sees the reply in the document editor before sending.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact UID of the email to reply to from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply draft body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "reply_all": {"type": "boolean", "description": "Whether to reply all instead of replying only to sender"},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_emails",
            "description": "List emails from an account/folder, newest first; date_from is inclusive and date_to is exclusive, so today means today-to-tomorrow and last month means month-start to current-month-start. Returns subject, sender, date, UID, account, and summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "max_results": {"type": "integer", "description": "Max emails to return (default: 20)"},
                    "limit": {"type": "integer", "description": "Backward-compatible alias for max_results"},
                    "unread_only": {"type": "boolean", "description": "Only show unread emails. Default false; set true only when the user asks for unread emails."},
                    "unresponded_only": {"type": "boolean", "description": "Only show unanswered emails. Default false."},
                    "date_from": {"type": "string", "description": "Inclusive ISO date/datetime lower bound, e.g. 2026-07-01 for last-month filtering."},
                    "date_to": {"type": "string", "description": "Exclusive ISO date/datetime upper bound, e.g. 2026-08-01 for last-month filtering."},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "Search email subjects, senders, and message bodies by topic or person, then use read_email with a returned UID when full message content is needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic, person, sender, or phrase to find"},
                    "folder": {"type": "string", "description": "Limit search to this IMAP folder; omit to search across mailbox folders"},
                    "max_results": {"type": "integer", "description": "Maximum matching messages to return (default: 20)"},
                    "days_back": {"type": "integer", "description": "Optional positive lookback window in days; omit to search the available mailbox history"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts"},
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Read the full content of a specific email by UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID to read"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_attachment",
            "description": "Read/download an email attachment using the UID, index, account and folder from read_email. Returns extracted PDF, DOCX, XLSX and text contents inline. Open relevant attachments when the email body does not answer the question. Reports extraction limitations explicitly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "index": {"type": "integer", "description": "Attachment index from read_email's attachment list, usually 0 for the first attachment"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid", "index"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scan_email_unsubscribes",
            "description": "Scan up to 500 newest email headers for likely spam/newsletter unsubscribe candidates. Does not unsubscribe anything. Review candidates with the user before acting; mailto methods can be executed with unsubscribe_email, web URL methods require browser/web tools after approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "IMAP folder to scan (default: INBOX)"},
                    "limit": {"type": "integer", "description": "Maximum candidates to return (default: 25)"},
                    "max_scan": {"type": "integer", "description": "How many newest emails to inspect, capped at 500 (default: 500)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scan_spam",
            "description": "Review recent inbox messages for likely spam/phishing. Returns candidates with UID, sender, subject, score, and reasons. Does not move/delete/block anything; ask the user to confirm before bulk_email action=junk or block_sender.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "IMAP folder to scan, default INBOX"},
                    "limit": {"type": "integer", "description": "Maximum candidates to return, default 10"},
                    "max_scan": {"type": "integer", "description": "How many newest messages to inspect, default 100"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "unsubscribe_email",
            "description": "Execute one approved unsubscribe action for an email UID. Safe mailto List-Unsubscribe methods are sent/staged. Web URL methods return a requires-browser instruction and exact URL; use browser/web tools only after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from scan_email_unsubscribes/list_emails"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "method_index": {"type": "integer", "description": "Method index from scan_email_unsubscribes (default: 0)"},
                    "allow_web": {"type": "boolean", "description": "Return browser/web instructions when selected method is URL"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": "SEND a reply email immediately by UID. Do not use this when the user asks to write/draft/open/start a reply; use draft_email_reply so the user can review in the Odysseus document editor. Only use when the user explicitly says to send now. Use the exact UID from the latest read_email/list_emails result; never invent UID 1. Automatically threads with In-Reply-To/References headers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact UID of the email to reply to from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_email",
            "description": "Perform one action on many emails at once. Use this for 'delete all those', 'archive these', 'mark all read', 'move these to spam/junk', or any bulk operation after list_emails. For suspected spam, show the candidates/reasons and get user confirmation before moving or deleting. Always pass account when the listed emails came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"], "description": "Bulk action to perform"},
                    "uids": {"type": "array", "items": {"type": "string"}, "description": "UIDs from the latest list_emails result"},
                    "all_unread": {"type": "boolean", "description": "Operate on all unread messages in folder instead of explicit UIDs"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "permanent": {"type": "boolean", "description": "For delete: hard-delete instead of moving to Trash"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "block_sender",
            "description": "Block one or more email senders after user approval. Records a local sender block rule and optionally moves matching current messages to Junk/Spam. Use after listing/scanning suspected spam and confirming the user wants to block the sender.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender": {"type": "string", "description": "Sender email address to block"},
                    "uids": {"type": "array", "items": {"type": "string"}, "description": "Email UIDs whose senders should be blocked"},
                    "folder": {"type": "string", "description": "Source folder for UID lookup/current-message moves, default INBOX"},
                    "reason": {"type": "string", "description": "Short reason for the block, e.g. phishing, scam, unsolicited sales"},
                    "move_existing": {"type": "boolean", "description": "Move matching current messages to Junk/Spam, default true"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_email_state",
            "description": "Compact reversible email state manager. Use for favorite/unfavorite, done/undone, unarchive, list blocked senders, and unblock sender; use mark_email_read for read/unread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["favorite", "unfavorite", "mark_read", "mark_unread", "mark_done", "mark_undone", "unarchive", "list_blocked", "unblock_sender"]},
                    "uid": {"type": "string", "description": "Email UID for message actions"},
                    "sender": {"type": "string", "description": "Sender email address for unblock_sender"},
                    "folder": {"type": "string", "description": "Source folder, default INBOX except unarchive defaults Archive"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_email",
            "description": "Delete one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "permanent": {"type": "boolean", "description": "Hard-delete instead of moving to Trash"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_email",
            "description": "Archive one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_email_read",
            "description": "Mark one email read or unread by UID; always include read=true for read and read=false for unread. For multiple messages, use bulk_email instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "read": {"type": "boolean", "description": "True marks read; false marks unread"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_bg_jobs",
            "description": "Inspect and control detached background `bash` jobs (started with the `#!bg` marker). action='list' shows this chat's jobs with id/status/age/command; action='output' returns a job's captured output so far (use for a still-running job, or to re-read a finished one); action='kill' terminates a runaway job's process tree instead of waiting out its max-runtime. output and kill need job_id from list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "output", "kill"], "description": "list | output | kill (default: list)"},
                    "job_id": {"type": "string", "description": "Background job id (required for output/kill; from action='list')"},
                },
                "required": ["action"]
            }
        }
    },
]


# ---------------------------------------------------------------------------
# Converter: native function call -> ToolBlock
# ---------------------------------------------------------------------------

def _decode_loose_json_string(value: str) -> str:
    """Decode common JSON string escapes without requiring inner quotes to be escaped."""
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\" or i + 1 >= len(value):
            out.append(ch)
            i += 1
            continue
        nxt = value[i + 1]
        if nxt == "n":
            out.append("\n")
        elif nxt == "r":
            out.append("\r")
        elif nxt == "t":
            out.append("\t")
        elif nxt == "b":
            out.append("\b")
        elif nxt == "f":
            out.append("\f")
        elif nxt in ('"', "\\", "/"):
            out.append(nxt)
        elif nxt == "u" and i + 5 < len(value):
            try:
                out.append(chr(int(value[i + 2:i + 6], 16)))
                i += 4
            except ValueError:
                out.append("\\" + nxt)
        else:
            out.append("\\" + nxt)
        i += 2
    return "".join(out)


def _repair_document_function_args(tool_type: str, arguments: str) -> Optional[dict]:
    """Salvage obvious malformed document tool args from local model wrappers.

    The doc LoRA sometimes emits the right native tool call but puts raw quotes
    inside the document text, making the surrounding JSON invalid. Treat that as
    a wrapper parse failure, not a semantic tool-choice failure.
    """
    if tool_type != "update_document" or not isinstance(arguments, str):
        return None
    raw = arguments.strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        return None
    for key in ("content", "conten"):
        marker = f'"{key}"'
        key_pos = raw.find(marker)
        if key_pos < 0:
            continue
        colon_pos = raw.find(":", key_pos + len(marker))
        if colon_pos < 0:
            continue
        first_quote = raw.find('"', colon_pos + 1)
        if first_quote < 0:
            continue
        close_brace = raw.rfind("}")
        last_quote = raw.rfind('"', first_quote + 1, close_brace)
        if last_quote <= first_quote:
            continue
        content = _decode_loose_json_string(raw[first_quote + 1:last_quote])
        return {"content": content}
    return None


def _repair_truncated_readonly_inspect_args(
    tool_type: str, arguments: Any
) -> Optional[dict[str, Any]]:
    """Recover complete fields from a truncated read-only media inspection."""
    if tool_type != "inspect_media" or not isinstance(arguments, str):
        return None
    raw = arguments.strip()
    if not raw.startswith("{"):
        return None

    top_level_commas: list[int] = []
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 1:
            top_level_commas.append(index)

    candidates = [raw + "}"] + [
        raw[:index].rstrip() + "}" for index in reversed(top_level_commas)
    ]
    for candidate in candidates:
        try:
            repaired = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(repaired, dict) or not str(repaired.get("path") or "").strip():
            continue
        # Never infer or salvage an artifact-producing media operation.
        if any(
            repaired.get(key) not in (None, "", [], {})
            for key in ("output_path", "exports", "timestamp_path", "caption")
        ):
            return None
        return repaired
    return None


def function_call_to_tool_block(name: str, arguments: str) -> Optional[ToolBlock]:
    """Convert a native function call into a ToolBlock for the existing execution pipeline."""
    tool_type = _TOOL_NAME_MAP.get(name, name)
    try:
        if not arguments or (isinstance(arguments, str) and not arguments.strip()):
            args = {}
        else:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError):
        args = _repair_document_function_args(tool_type, arguments)
        if args is None:
            args = _repair_truncated_readonly_inspect_args(tool_type, arguments)
        if args is not None:
            logger.warning(f"Repaired malformed function call arguments for {name}")
        else:
            logger.error(f"Failed to parse function call arguments for {name}: {arguments}")
            return None

    # Some models emit valid JSON that isn't an object (e.g. a bare array
    # ["ls -la"], string, or number) as function arguments. Most local tools keep
    # the legacy empty-object coercion for stream robustness, but email MCP tools
    # must fail closed so a malformed call cannot read the default mailbox.
    # Uses the shared BUILTIN_EMAIL_TOOLS (single source of truth) so the
    # fail-closed set can't drift from the dispatch/blocklist sets.
    if not isinstance(args, dict):
        if tool_type.startswith("mcp__email__") or name in BUILTIN_EMAIL_TOOLS:
            logger.warning(f"Non-object email function call arguments for {name}: {args!r}; rejecting")
            return None
        logger.warning(f"Non-object function call arguments for {name}: {args!r}; treating as empty")
        args = {}

    tool_type, args = normalize_native_function_args(name, args)

    if tool_type == "web_fetch" and isinstance(args.get("urls"), list):
        # Some compact-model calls encode a URL as [url, label] inside the
        # batch. The first value is still an explicit HTTP(S) URL and the
        # second is display-only prose, so this two-string shape is
        # unambiguous. Normalize it without accepting arbitrary nested data.
        normalized_urls = []
        for item in args["urls"]:
            if (
                isinstance(item, list)
                and len(item) in (1, 2)
                and isinstance(item[0], str)
                and item[0].strip().lower().startswith(("http://", "https://"))
                and (len(item) == 1 or isinstance(item[1], str))
            ):
                normalized_urls.append(item[0])
            elif isinstance(item, list):
                logger.warning("Rejecting ambiguous nested web_fetch URL item: %r", item)
                return None
            else:
                normalized_urls.append(item)
        args["urls"] = normalized_urls

    # Compact schema projection preserves top-level shapes but may omit nested
    # `required` constraints. Never let a malformed still export silently
    # become a frame from time zero at the converter boundary.
    semantic_error = normalized_native_function_argument_error(tool_type, args)
    if semantic_error:
        logger.warning("Rejecting native function arguments: %s", semantic_error)
        return None

    # A URL is never a valid local file path. Small models occasionally choose
    # read_file for PDF/document URLs; route that exact transport mistake to
    # the native URL reader instead of attempting a nonsense workspace path.
    if tool_type == "read_file":
        path = str(args.get("path") or "").strip()
        if path.lower().startswith(("http://", "https://")):
            logger.info("Redirecting read_file URL to web_fetch: %s", path[:160])
            tool_type = "web_fetch"
            args = {"url": path, "full": True}
        else:
            path_without_query = path.split("?", 1)[0].split("#", 1)[0]
            suffix = "." + path_without_query.rsplit(".", 1)[-1].lower() if "." in path_without_query else ""
            if suffix in _BINARY_VISUAL_MEDIA_SUFFIXES:
                logger.info("Redirecting binary-media read_file to inspect_media: %s", path[:160])
                tool_type = "inspect_media"
                args = {"path": path}

    # web_fetch is an HTTP reader, while task-local HTML needs a rendered DOM.
    # Compact models sometimes choose the former for file:///workspace pages;
    # preserve their exact target but route the transport to Odysseus' native
    # private browser instead of spending a failed tool round.
    if tool_type == "web_fetch":
        url = str(args.get("url") or "").strip()
        path_part = url.lower().split("?", 1)[0].split("#", 1)[0]
        if (
            url.lower().startswith("file:///workspace/")
            and path_part.endswith((".html", ".htm"))
        ):
            logger.info("Redirecting local HTML web_fetch to private_browser: %s", url[:160])
            tool_type = "private_browser"
            args = {"action": "open", "url": url}

    if tool_type.startswith("mcp__email__") or name in BUILTIN_EMAIL_TOOLS:
        for numeric_key in ("max_results", "limit", "offset"):
            value = args.get(numeric_key)
            if isinstance(value, str) and value.strip().isdigit():
                args[numeric_key] = int(value.strip())

    required_args = _REQUIRED_NATIVE_TOOL_ARGS.get(tool_type)
    if required_args and not any(str(args.get(key) or "").strip() for key in required_args):
        logger.warning(f"Rejecting empty required arguments for function call {name}: {args!r}")
        return None

    # Allow MCP tools through (namespaced as mcp__serverid__toolname)
    if tool_type.startswith("mcp__"):
        content = json.dumps(args) if args else "{}"
        return ToolBlock(tool_type, content)
    # Email tools are implemented as MCP — route them to email
    if name in BUILTIN_EMAIL_TOOLS:
        return ToolBlock(f"mcp__email__{name}", json.dumps(args) if args else "{}")
    if tool_type not in TOOL_TAGS:
        logger.warning(f"Unknown function call: {name}")
        return None

    # Convert structured args back to the text format each tool expects
    if tool_type == "bash":
        content = args.get("command", "")
    elif tool_type == "host_shell":
        content = json.dumps(args)
    elif tool_type == "python":
        content = args.get("code", "")
    elif tool_type == "web_search":
        # ``query`` is the canonical schema field.  Some native wrappers also
        # include ``command": "web_search"`` as transport metadata; treating
        # that metadata as the query silently searches for the tool's name.
        # Keep legacy aliases only as fallbacks when the canonical field is
        # absent.
        content = args.get("query", "")
        queries = args.get("queries")
        if not content and isinstance(queries, list) and queries:
            content = str(queries[0])
        elif not content and queries:
            content = str(queries)
        elif not content and args.get("command"):
            content = args.get("command", "")
        # Preserve the model-requested freshness filter — the web_search schema
        # advertises time_filter and the executor parses {"query","time_filter"},
        # but a bare query string dropped it. Mirrors the read_file JSON idiom.
        tf = args.get("time_filter")
        if content and isinstance(tf, str) and tf in ("day", "week", "month", "year"):
            content = json.dumps({"query": content, "time_filter": tf})
    elif tool_type == "read_file":
        # Plain path (back-compat) unless a line range is requested → JSON.
        if args.get("offset") or args.get("limit"):
            content = json.dumps(args)
        else:
            content = args.get("path", "")
    elif tool_type in ("grep", "glob", "ls"):
        content = json.dumps(args) if args else "{}"
    elif tool_type == "get_workspace":
        content = ""
    elif tool_type == "write_file":
        body = args.get("content")
        if isinstance(body, str) and body.strip():
            content = args.get("path", "") + "\n" + body
        else:
            # Preserve missing/empty intent for WriteFileTool instead of folding
            # all empty shapes into the same path-plus-newline representation.
            content = json.dumps(args)
    elif tool_type == "edit_file":
        content = json.dumps(args)
    elif tool_type == "apply_patch":
        content = args.get("patch_text") or args.get("patchText") or args.get("patch") or ""
    elif tool_type == "todowrite":
        content = json.dumps(args)
    elif tool_type == "create_document":
        parts = [args.get("title", "Untitled")]
        language = str(args.get("language") or "").strip().casefold()
        # A common model slip is treating this editor-format field as a human
        # language and emitting ``en``/``English``.  The legacy line transport
        # interpreted an unknown second line as document content, visibly
        # prepending it to the user's prose.  Preserve the document body and
        # let the executor's content sniffer select markdown instead.
        if language not in {"en", "eng", "english"} and language:
            parts.append(language)
        parts.append(args.get("content", ""))
        content = "\n".join(parts)
    elif tool_type == "edit_document":
        if args.get("command"):
            content = args.get("command", "")
        else:
            blocks = []
            edits = args.get("edits", [])
            if not isinstance(edits, list):
                edits = []
            alias_find = (
                args.get("find")
                or args.get("old_string")
                or args.get("oldString")
                or args.get("pattern")
            )
            alias_replace = (
                args.get("replace")
                or args.get("new_string")
                or args.get("newString")
                or args.get("replacement")
            )
            if alias_find is not None and alias_replace is not None:
                edits = [{"find": str(alias_find), "replace": str(alias_replace)}, *edits]
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                marker = "REPLACE_ALL" if edit.get("replace_all") is True else "REPLACE"
                blocks.append(
                    f'<<<FIND>>>\n{edit.get("find", "")}\n<<<{marker}>>>\n{edit.get("replace", "")}\n<<<END>>>'
                )
            content = "\n".join(blocks)
    elif tool_type == "suggest_document":
        blocks = []
        suggestions = args.get("suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            blocks.append(
                f'<<<FIND>>>\n{s.get("find", "")}\n<<<SUGGEST>>>\n{s.get("replace", "")}\n<<<REASON>>>\n{s.get("reason", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "update_document":
        content = args.get("content", "")
    elif tool_type == "search_chats":
        content = args.get("query", "")
    elif tool_type == "chat_with_model":
        content = args.get("model", "") + "\n" + args.get("message", "")
    elif tool_type == "create_session":
        content = args.get("name", "Untitled") + "\n" + args.get("model", "")
    elif tool_type == "list_sessions":
        content = args.get("filter", "")
    elif tool_type == "send_to_session":
        content = args.get("session_id", "") + "\n" + args.get("message", "")
    elif tool_type == "pipeline":
        # Pass as JSON for the pipeline parser
        content = json.dumps({"steps": args.get("steps", [])})
    elif tool_type == "manage_session":
        action = args.get("action", "")
        value = args.get("value", "")
        # `list` is the only action that takes an OPTIONAL keyword
        # filter — never a session_id. Don't leak the "current" default
        # into the filter slot (was producing "No sessions found
        # matching 'current'" when the agent omitted session_id).
        if action == "list":
            keyword = args.get("session_id", "") or args.get("keyword", "") or value
            content = "list" + (("\n" + keyword) if keyword and keyword.lower() != "current" else "")
        else:
            sid = args.get("session_id", "current")
            content = action + "\n" + sid
            if value:
                content += "\n" + value
    elif tool_type == "manage_memory":
        if args.get("command"):
            command = str(args.get("command") or "").strip()
            action = str(args.get("action") or "").strip().lower()
            first = command.splitlines()[0].strip().lower() if command else ""
            # `command` is documented as either a complete line-form command
            # or an action-specific payload. When `action` is supplied, do
            # not discard it and reinterpret a search query as the operation.
            content = command if not action or first == action else f"{action}\n{command}"
        else:
            action = args.get("action", "")
            if action == "add":
                text = args.get("text") or args.get("value") or args.get("content") or ""
                if not text and args.get("key"):
                    text = str(args.get("key") or "")
                content = "add\n" + str(text)
                if args.get("category"):
                    content += "\n" + args["category"]
                elif args.get("key"):
                    content += "\n" + str(args["key"])
            elif action == "edit":
                content = "edit\n" + args.get("memory_id", "") + "\n" + args.get("text", "")
            elif action == "delete":
                content = "delete\n" + args.get("memory_id", "")
            elif action == "search":
                content = "search\n" + (args.get("text") or args.get("tex") or args.get("query") or "")
            elif action == "list":
                content = "list"
                if args.get("category"):
                    content += "\n" + args["category"]
            else:
                content = action
    elif tool_type == "list_models":
        content = args.get("filter", "")
    elif tool_type == "ui_control":
        action = args.get("action", "")
        name = args.get("name", "")
        value = args.get("value", "")
        if action == "toggle":
            content = f"toggle {name} {value}"
        elif action == "open_panel":
            content = f"open_panel {name or value}"
            if args.get("view"):
                content += f" {args['view']}"
        elif action == "open_email_reply":
            uid = args.get("uid") or name
            folder = args.get("folder") or value or "INBOX"
            mode = args.get("mode") or "reply"
            content = f"open_email_reply {uid} {folder} {mode}"
            body = args.get("body") or args.get("extra") or args.get("content") or ""
            if body:
                content += f" {body}"
        elif action == "set_mode":
            content = f"set_mode {value or name}"
        elif action == "switch_model":
            content = f"switch_model {value or name}"
        elif action == "set_theme":
            content = f"set_theme {value or name}"
        elif action == "create_theme":
            content = json.dumps({"action": action, "name": name or value or "custom",
                                  "colors": args.get("colors", {}), "background": args.get("background")})
        else:
            content = action
    elif tool_type in ("manage_tasks", "manage_skills", "api_call",
                        "manage_endpoints", "manage_mcp", "manage_webhooks",
                        "manage_tokens", "manage_documents", "manage_settings",
                        "download_model", "serve_model", "stop_served_model",
                        "tail_serve_output", "list_cached_models",
                        "list_serve_presets", "list_cookbook_servers",
                        "list_downloads", "manage_research",
                        "trigger_research"):
        content = json.dumps(args)
    elif tool_type == "ask_teacher":
        content = args.get("model", "auto") + "\n" + args.get("problem", "")
    elif tool_type == "ask_user":
        # Keep user-facing labels readable in the tool trace.  The outer SSE
        # JSON encoder will escape them for transport and JSON.parse restores
        # them once; pre-escaping here caused literal ``\u00f1`` sequences to
        # remain visible in the debug panel.
        content = json.dumps(args, ensure_ascii=False)
    else:
        content = json.dumps(args)

    return ToolBlock(tool_type, content)
