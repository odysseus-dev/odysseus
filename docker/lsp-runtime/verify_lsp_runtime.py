"""Offline container acceptance probe for Odysseus's pinned LSP MCP runtime."""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RUNTIME_ENV = {
    "LSP_MCP_AUTO_UPDATE": "false",
    "LSP_MCP_PYTHON_PROVIDER": "pyright-mcp",
    "LSP_MCP_TYPESCRIPT_ENABLED": "true",
    "LSP_MCP_VUE_ENABLED": "false",
    "LSP_MCP_BACKEND_RUNTIME_MODE": "registry",
    "npm_config_offline": "true",
    "PATH": "/opt/odysseus-lsp/bin:/opt/odysseus-lsp/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
}
REQUIRED_TOOLS = {
    "status",
    "list_backends",
    "start_backend",
    "switch_workspace_for_language",
    "diagnostics",
    "definition",
    "references",
    "rename",
}
PYTHON_DEFINED_SYMBOL_CALL = {
    "file": "/workspace/lsp_fixture/python_fixture.py",
    "line": 6,
    "column": 12,
}
TYPESCRIPT_DEFINED_SYMBOL_CALL = {
    "file": "/workspace/lsp_fixture/ts_fixture.ts",
    "line": 5,
    "column": 35,
}


def _text(result) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def _call(session, name: str, arguments: dict) -> str:
    result = await session.call_tool(name, arguments)
    payload = _text(result)
    if getattr(result, "isError", False) or '"success": false' in payload:
        raise RuntimeError(f"{name} failed: {payload}")
    return payload


async def main() -> None:
    params = StdioServerParameters(
        command="node",
        args=["/opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js"],
        env=RUNTIME_ENV,
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            missing = sorted(REQUIRED_TOOLS - names)
            if missing:
                raise RuntimeError(f"required tools missing: {', '.join(missing)}")
            print(f"INIT_TOOL_COUNT={len(names)}")
            schema_by_name = {
                tool.name: tool.inputSchema
                for tool in tools.tools
                if tool.name in {"definition", "references", "rename"}
            }
            print(f"SEMANTIC_TOOL_SCHEMAS={json.dumps(schema_by_name, sort_keys=True)}")

            for language in ("python", "typescript"):
                await _call(
                    session,
                    "switch_workspace_for_language",
                    {"language": language, "path": "/workspace/lsp_fixture"},
                )
                payload = await _call(session, "start_backend", {"language": language})
                print(f"START_{language.upper()}={payload}")

            python_diagnostics = await _call(
                session,
                "diagnostics",
                {
                    "path": "/workspace/lsp_fixture/python_fixture.py",
                    "summary_only": True,
                    "page_size": 10,
                },
            )
            if "undefined_symbol" not in python_diagnostics:
                raise RuntimeError(f"expected Python diagnostic not found: {python_diagnostics}")
            print(f"PYTHON_DIAGNOSTICS={python_diagnostics}")

            definition = await _call(
                session, "definition", PYTHON_DEFINED_SYMBOL_CALL
            )
            if (
                "Definition(s)" not in definition
                or "/workspace/lsp_fixture/python_fixture.py:1:5" not in definition
            ):
                raise RuntimeError(f"expected Python definition not found: {definition}")
            print(f"PYTHON_DEFINITION={definition}")

            references = await _call(
                session,
                "references",
                {
                    **PYTHON_DEFINED_SYMBOL_CALL,
                    "page_size": 20,
                },
            )
            if "defined_symbol" not in references or "Found 2 reference(s)" not in references:
                raise RuntimeError(f"expected Python references not found: {references}")
            print(f"PYTHON_REFERENCES={references}")

            python_rename = await _call(
                session,
                "rename",
                {
                    **PYTHON_DEFINED_SYMBOL_CALL,
                    "newName": "renamed_symbol",
                },
            )
            if (
                "Rename Preview" not in python_rename
                or "renamed_symbol" not in python_rename
                or "Found 2 occurrence(s)" not in python_rename
            ):
                raise RuntimeError(
                    f"expected Python rename workspace edit not found: {python_rename}"
                )
            print(f"PYTHON_RENAME_DRY_RUN={python_rename}")

            ts_diagnostics = await _call(
                session,
                "diagnostics",
                {
                    "path": "/workspace/lsp_fixture/ts_fixture.ts",
                    "summary_only": False,
                    "page_size": 10,
                },
            )
            if "not assignable" not in ts_diagnostics and "Type" not in ts_diagnostics:
                raise RuntimeError(f"expected TypeScript diagnostic not found: {ts_diagnostics}")
            print(f"TS_DIAGNOSTICS={ts_diagnostics}")

            ts_definition = await _call(
                session, "definition", TYPESCRIPT_DEFINED_SYMBOL_CALL
            )
            if '"name":"definedTsSymbol"' not in ts_definition:
                raise RuntimeError(
                    f"expected TypeScript definition not found: {ts_definition}"
                )
            print(f"TS_DEFINITION={ts_definition}")

            rename = await _call(
                session,
                "rename",
                {
                    **TYPESCRIPT_DEFINED_SYMBOL_CALL,
                    "newName": "renamedTsSymbol",
                },
            )
            if (
                '"preview":true' not in rename
                or "renamedTsSymbol" not in rename
                or '"totalLocations":2' not in rename
            ):
                raise RuntimeError(f"expected rename workspace edit not found: {rename}")
            print(f"TS_RENAME_DRY_RUN={rename}")


if __name__ == "__main__":
    asyncio.run(main())
