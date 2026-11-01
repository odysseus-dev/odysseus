"""Helpers for the experimental Spec driver, whose execution is disabled."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import httpx

from src import spec_router
from src.agent_runtime.authority import MISSING_AUTHORITY, with_request_authority
from src.clean_agent_preview import (
    event, preview_model_response, preview_tool_result_text,
)
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_execution import execute_tool_block
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block

logger = logging.getLogger(__name__)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(str(p.get('text') or '') for p in content
                         if isinstance(p, dict) and p.get('type') == 'text')
    return ''


def _name(schema):
    return (schema.get('function') or {}).get('name') or ''


# JSON-schema keywords the provider's guided decoding (named tool_choice) cannot compile.
_GRAMMAR_UNSUPPORTED = frozenset({
    'uniqueItems', 'contains', 'minContains', 'maxContains', 'patternProperties', 'propertyNames',
    'dependentRequired', 'dependentSchemas', 'if', 'then', 'else',
    'unevaluatedProperties', 'unevaluatedItems',
})


def grammar_safe(schema):
    """Copy of a schema without keywords guided decoding rejects (validation still runs at execution)."""
    if isinstance(schema, dict):
        return {k: grammar_safe(v) for k, v in schema.items() if k not in _GRAMMAR_UNSUPPORTED}
    if isinstance(schema, list):
        return [grammar_safe(v) for v in schema]
    return schema


def spec_surface(disabled_tools):
    disabled = set(disabled_tools or ())
    return [s for s in FUNCTION_TOOL_SCHEMAS if _name(s) and _name(s) not in disabled]


async def _model_round(client, endpoint_url, headers, request, recovery):
    """Run one chat-model round; return (content, tool_calls, usage)."""
    content, calls, usage, raw = '', {}, {}, []
    async with preview_model_response(client, endpoint_url, headers, request, recovery) as response:
        async for line in response.aiter_lines():
            if len(raw) < 4 and line.strip():
                raw.append(line[:400])
            if line.strip() and not line.startswith('data: '):
                logger.warning('[spec-driver] unexpected stream line: %s', line[:500])
            if not line.startswith('data: ') or line.strip() == 'data: [DONE]':
                continue
            try:
                chunk = json.loads(line[6:])
            except ValueError:
                continue
            if chunk.get('error') or chunk.get('object') == 'error':
                raise RuntimeError(f"model stream error: {str(chunk.get('error') or chunk)[:500]}")
            usage = chunk.get('usage') or usage
            for choice in chunk.get('choices') or []:
                delta = choice.get('delta') or {}
                text = delta.get('content') or ''
                content += text
                for frag in delta.get('tool_calls') or []:
                    call = calls.setdefault(frag.get('index', 0), {'id': '', 'name': '', 'arguments': ''})
                    call['id'] = frag.get('id') or call['id']
                    fn = frag.get('function') or {}
                    call['name'] += fn.get('name') or ''
                    call['arguments'] += fn.get('arguments') or ''
    if not calls:
        logger.warning('[spec-driver] no tool call; status=%s max_tokens=%s msgs=%s first lines=%s',
                       getattr(response, 'status_code', None), request.get('max_tokens'),
                       [(m.get('role'), type(m.get('content')).__name__,
                         [p.get('type') for p in m['content']] if isinstance(m.get('content'), list) else len(str(m.get('content') or '')))
                        for m in request.get('messages', [])], raw[-2:])
    return content, [calls[i] for i in sorted(calls)], usage


@with_request_authority
async def stream_spec_turn(*, endpoint_url, model, messages, headers, session_id, owner,
                           disabled_tools, tool_policy, workspace=None, active_document=None,
                           client_runtime_context=None, external_untrusted_context_seen=False,
                           max_tokens=768, max_rounds=8, temperature=0.0,
                           request_authority=MISSING_AUTHORITY, **ignored):
    # This experimental runtime lacks the normal turn and completion gates.
    # Refuse direct callers too, before contacting a router or executor.
    yield event({'delta': 'Spec driver mode is unavailable because its authority and completion checks are not supported. No action was performed.'})
    yield 'data: [DONE]\n\n'
    return
