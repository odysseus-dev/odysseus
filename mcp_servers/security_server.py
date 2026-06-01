"""
security_server.py

MCP server exposing nuclei (vulnerability scanner) and katana (web crawler)
for security testing and reconnaissance.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("security")

_initialized = False
_nuclei_bin = None
_katana_bin = None


def _ensure_init():
    """Find security tool binaries."""
    global _nuclei_bin, _katana_bin, _initialized
    if _initialized:
        return
    _initialized = True

    for name, attr in [("nuclei", "_nuclei_bin"), ("katana", "_katana_bin")]:
        candidates = [
            f"/app/data/bin/{name}",
            f"/usr/local/bin/{name}",
            os.path.expanduser(f"~/.local/bin/{name}"),
        ]
        for c in candidates:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                globals()[attr] = c
                break


def _truncate(text: str, limit: int = 10000) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="scan_target",
            description="Run nuclei vulnerability scanner against a target URL/IP. Uses templates from the nuclei community. Returns findings with severity levels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target URL or IP to scan (e.g. 'https://example.com' or '192.168.1.1')",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Filter by severity: critical,high,medium,low,info (comma-separated, default: critical,high,medium)",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Filter templates by tags (e.g. 'cve,xss,sqli' or 'tech')",
                    },
                    "templates": {
                        "type": "string",
                        "description": "Specific template path or URL to use",
                    },
                    "rate_limit": {
                        "type": "integer",
                        "description": "Requests per second (default: 150)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Scan timeout in seconds (default: 300)",
                    },
                },
                "required": ["target"],
            },
        ),
        Tool(
            name="crawl_site",
            description="Crawl a website using katana to discover URLs, endpoints, and parameters. Useful for reconnaissance before security testing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL to crawl (e.g. 'https://example.com')",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Crawl depth (default: 3)",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages to crawl (default: 100)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["same-domain", "subdomain", "url"],
                        "description": "Crawl scope (default: same-domain)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Crawl timeout in seconds (default: 120)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="update_templates",
            description="Update nuclei templates to the latest version from the community.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_init()

    try:
        if name == "scan_target":
            return await _scan(arguments)
        elif name == "crawl_site":
            return await _crawl(arguments)
        elif name == "update_templates":
            return await _update_templates()
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def _scan(args: dict) -> list[TextContent]:
    target = args.get("target", "")
    if not target:
        return [TextContent(type="text", text="Error: target is required")]

    if not _nuclei_bin:
        return [TextContent(type="text", text="Error: nuclei binary not found.")]

    cmd = [_nuclei_bin, "-u", target, "-silent", "-jsonl", "-nc"]

    severity = args.get("severity", "critical,high,medium")
    if severity:
        cmd.extend(["-severity", severity])

    tags = args.get("tags", "")
    if tags:
        cmd.extend(["-tags", tags])

    templates = args.get("templates", "")
    if templates:
        cmd.extend(["-t", templates])

    rate_limit = args.get("rate_limit", 150)
    cmd.extend(["-rl", str(rate_limit)])

    timeout = args.get("timeout", 300)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0 and not output:
            return [TextContent(type="text", text=f"Scan failed (exit {proc.returncode}): {errors[:500]}")]

        if not output:
            return [TextContent(type="text", text=f"Scan complete for {target}. No findings matched severity filter: {severity}")]

        # Parse JSONL results
        findings = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                finding = json.loads(line)
                severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(
                    finding.get("info", {}).get("severity", ""), "❓"
                )
                name = finding.get("info", {}).get("name", "unknown")
                matched = finding.get("matched-at", finding.get("matched", "?"))
                findings.append(f"{severity_icon} [{finding.get('info', {}).get('severity', '?').upper()}] {name} @ {matched}")
            except json.JSONDecodeError:
                findings.append(line)

        result = f"Scan results for {target} ({len(findings)} findings):\n\n" + "\n".join(findings[:100])
        if len(findings) > 100:
            result += f"\n... and {len(findings) - 100} more"

        return [TextContent(type="text", text=_truncate(result))]

    except asyncio.TimeoutError:
        return [TextContent(type="text", text=f"Scan timed out after {timeout}s")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def _crawl(args: dict) -> list[TextContent]:
    url = args.get("url", "")
    if not url:
        return [TextContent(type="text", text="Error: url is required")]

    if not _katana_bin:
        return [TextContent(type="text", text="Error: katana binary not found.")]

    cmd = [_katana_bin, "-u", url, "-silent", "-jsonl"]

    depth = args.get("depth", 3)
    cmd.extend(["-d", str(depth)])

    max_pages = args.get("max_pages", 100)
    cmd.extend(["-c", str(max_pages)])

    scope = args.get("scope", "same-domain")
    if scope == "subdomain":
        cmd.extend(["-fs", "rdn"])
    elif scope == "url":
        cmd.extend(["-fs", "url"])

    timeout = args.get("timeout", 120)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        output = stdout.decode("utf-8", errors="replace").strip()

        if not output:
            return [TextContent(type="text", text=f"Crawl complete for {url}. No URLs discovered.")]

        # Parse results
        urls = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                url_str = item.get("url", item.get("endpoint", line))
                method = item.get("method", "GET")
                status = item.get("status_code", "")
                urls.append(f"[{method}] {url_str}" + (f" ({status})" if status else ""))
            except json.JSONDecodeError:
                urls.append(line)

        result = f"Crawl results for {url} ({len(urls)} URLs found):\n\n" + "\n".join(urls[:200])
        if len(urls) > 200:
            result += f"\n... and {len(urls) - 200} more"

        return [TextContent(type="text", text=_truncate(result))]

    except asyncio.TimeoutError:
        return [TextContent(type="text", text=f"Crawl timed out after {timeout}s")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def _update_templates() -> list[TextContent]:
    if not _nuclei_bin:
        return [TextContent(type="text", text="Error: nuclei binary not found.")]

    try:
        proc = await asyncio.create_subprocess_exec(
            _nuclei_bin, "-update-templates",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()

        result = output or errors
        return [TextContent(type="text", text=f"Templates updated:\n{result}")]

    except asyncio.TimeoutError:
        return [TextContent(type="text", text="Template update timed out")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
