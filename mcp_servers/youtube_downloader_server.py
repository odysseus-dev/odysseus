"""
youtube_downloader_server.py

MCP server exposing the local YoutubeDownloader web service.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("youtube_downloader")

DEFAULT_BASE_URL = "http://127.0.0.1:49156"
REQUEST_TIMEOUT_SECONDS = 30.0


def _base_url() -> str:
    return os.environ.get("YTDL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _service_url(path: str) -> str:
    return f"{_base_url()}{path}"


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _normalize_single_download(arguments: dict) -> dict:
    payload = {}
    for key in ("video_link", "format", "quality", "folder", "name", "file_name"):
        value = arguments.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value not in (None, ""):
            payload[key] = value

    if "name" not in payload and "file_name" in payload:
        payload["name"] = payload.pop("file_name")
    elif "file_name" in payload:
        payload.pop("file_name")

    return payload


def _normalize_batch_download(arguments: dict) -> dict:
    videos = arguments.get("videos")
    if not isinstance(videos, list):
        return {"videos": videos}

    payload_videos = []
    for video in videos:
        if not isinstance(video, dict):
            payload_videos.append(video)
            continue
        payload_videos.append(_normalize_single_download(video))

    return {"videos": payload_videos}


def _validate_youtube_url(video_link: str) -> str | None:
    parsed = urlparse(video_link)
    host = parsed.netloc.lower()
    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        return "video_link must be a valid YouTube URL (youtube.com or youtu.be)."
    if not (
        host.endswith("youtube.com")
        or host.endswith("youtu.be")
        or host.endswith("www.youtube.com")
        or host.endswith("m.youtube.com")
    ):
        return "video_link must be a valid YouTube URL (youtube.com or youtu.be)."
    if "/playlist" in parsed.path or "list=" in parsed.query:
        return "Playlist download is not supported. Please provide a single video URL."
    return None


def _validate_single_download_payload(payload: dict) -> str | None:
    required_fields = [field for field in ("video_link", "format", "quality", "folder") if not payload.get(field)]
    if required_fields:
        return f"Missing required fields: {', '.join(required_fields)}"

    format_value = str(payload.get("format", "")).lower()
    if format_value not in ("mp4", "mp3"):
        return "format must be either 'mp4' or 'mp3'"

    url_error = _validate_youtube_url(str(payload.get("video_link", "")))
    if url_error:
        return url_error

    return None


def _validate_batch_download_payload(payload: dict) -> str | None:
    videos = payload.get("videos")
    if not isinstance(videos, list) or not videos:
        return "videos must be a non-empty array"

    for index, video in enumerate(videos):
        if not isinstance(video, dict):
            return f"videos[{index}] must be an object"
        error = _validate_single_download_payload(video)
        if error:
            return f"videos[{index}]: {error}"

    return None


async def _request_json(method: str, path: str, payload: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, headers={"Accept": "application/json"}) as client:
            response = await client.request(method, _service_url(path), json=payload)
    except httpx.RequestError as exc:
        return None, f"Error: Could not connect to YoutubeDownloader at {_base_url()}: {exc}"

    try:
        data = response.json()
    except ValueError:
        data = {"text": response.text}

    if response.is_success:
        return data, None

    return None, f"Error: YoutubeDownloader returned HTTP {response.status_code}: {_json_text(data)}"


@server.list_tools()
async def list_tools() -> list[Tool]:
    download_properties = {
        "video_link": {
            "type": "string",
            "description": "A single YouTube video URL from youtube.com or youtu.be.",
        },
        "format": {
            "type": "string",
            "enum": ["mp4", "mp3"],
            "description": "Desired output format.",
        },
        "quality": {
            "type": "string",
            "description": "Requested quality, such as 720p or 128kbps.",
        },
        "folder": {
            "type": "string",
            "description": "Destination folder on the local machine.",
        },
        "name": {
            "type": "string",
            "description": "Optional preferred file name stem.",
        },
        "file_name": {
            "type": "string",
            "description": "Optional alias for name.",
        },
    }

    return [
        Tool(
            name="queue_youtube_download",
            description="Queue a single YouTube video download through YoutubeDownloader.",
            inputSchema={
                "type": "object",
                "properties": download_properties,
                "required": ["video_link", "format", "quality", "folder"],
            },
        ),
        Tool(
            name="queue_youtube_batch_download",
            description="Queue a batch of YouTube video downloads through YoutubeDownloader.",
            inputSchema={
                "type": "object",
                "properties": {
                    "videos": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": download_properties,
                            "required": ["video_link", "format", "quality", "folder"],
                        },
                    }
                },
                "required": ["videos"],
            },
        ),
        Tool(
            name="get_youtube_download_task",
            description="Fetch the current status of a YoutubeDownloader task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task identifier returned by queue_youtube_download or queue_youtube_batch_download.",
                    }
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_youtube_downloader_health",
            description="Read the YoutubeDownloader health and queue snapshot.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "queue_youtube_download":
        payload = _normalize_single_download(arguments)
        error = _validate_single_download_payload(payload)
        if error:
            return [TextContent(type="text", text=f"Error: {error}")]

        data, request_error = await _request_json("POST", "/api/download", payload)
        if request_error:
            return [TextContent(type="text", text=request_error)]

        task_id = data.get("task_id", "unknown") if isinstance(data, dict) else "unknown"
        status = data.get("status", "queued") if isinstance(data, dict) else "queued"
        return [
            TextContent(
                type="text",
                text=(
                    f"Queued YoutubeDownloader task.\n"
                    f"Task ID: {task_id}\n"
                    f"Status: {status}\n"
                    f"Service: {_service_url('/api/download')}"
                ),
            )
        ]

    if name == "queue_youtube_batch_download":
        payload = _normalize_batch_download(arguments)
        error = _validate_batch_download_payload(payload)
        if error:
            return [TextContent(type="text", text=f"Error: {error}")]

        data, request_error = await _request_json("POST", "/api/download", payload)
        if request_error:
            return [TextContent(type="text", text=request_error)]

        task_id = data.get("task_id", "unknown") if isinstance(data, dict) else "unknown"
        status = data.get("status", "queued") if isinstance(data, dict) else "queued"
        video_count = data.get("video_count", len(payload["videos"])) if isinstance(data, dict) else len(payload["videos"])
        return [
            TextContent(
                type="text",
                text=(
                    f"Queued YoutubeDownloader batch task.\n"
                    f"Task ID: {task_id}\n"
                    f"Status: {status}\n"
                    f"Video count: {video_count}\n"
                    f"Service: {_service_url('/api/download')}"
                ),
            )
        ]

    if name == "get_youtube_download_task":
        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id:
            return [TextContent(type="text", text="Error: task_id is required")]

        data, request_error = await _request_json("GET", f"/api/task/{task_id}")
        if request_error:
            return [TextContent(type="text", text=request_error)]

        return [TextContent(type="text", text=_json_text(data))]

    if name == "get_youtube_downloader_health":
        data, request_error = await _request_json("GET", "/api/health")
        if request_error:
            return [TextContent(type="text", text=request_error)]

        return [TextContent(type="text", text=_json_text(data))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())