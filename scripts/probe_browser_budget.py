"""Isolated real-model/real-browser budget comparison; no live UI settings changed.

Uses only a fresh disposable browser and public shopping pages. No account
login, cart or purchase is requested. Explicit cleanup closes each browser.
"""
import os
import asyncio
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.clean_agent_preview import stream_preview
from src.agent_tools.web_tools import PrivateBrowserTool
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract, bind_turn_contract
from src.tool_policy import ToolPolicy


async def probe(limit):
    prompt = 'Go to ikea.com and find a yellow sofa. Give its name, price and product page. Do not accept optional cookies.'
    session = 'browser-budget-' + str(uuid.uuid4())
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'private_browser']
    contract = replace(resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
                       routing_experiment='recent_model_choice')
    row = {'call_limit': limit, 'round_limit': limit + 2, 'events': [], 'cleanup': False}
    # This probe contains public pages only. Retain bounded provider diagnostics,
    # never request headers, to distinguish context overflow from tool failures.
    real_client = httpx.AsyncClient
    async def record_response(response):
        request = json.loads(response.request.content)
        row.setdefault('provider_requests', []).append({
            'status': response.status_code, 'max_tokens': request.get('max_tokens'),
            'message_count': len(request.get('messages', [])),
            'request_chars': len(response.request.content),
            'original_request_present': any(m.get('role') == 'user' and m.get('content') == prompt
                                            for m in request.get('messages', [])),
        })
        if response.status_code >= 400:
            await response.aread()
            row.setdefault('provider_errors', []).append({
                'status': response.status_code, 'body': response.text[:1600],
                'message_count': len(request.get('messages', [])),
                'request_chars': len(response.request.content),
                'max_tokens': request.get('max_tokens'),
            })
    class DiagnosticClient(real_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, event_hooks={'response': [record_response]})
    start = time.monotonic()
    try:
        with bind_turn_contract(contract), patch('src.clean_agent_preview.INTERACTIVE_TOOL_CALL_LIMIT', limit), patch('src.clean_agent_preview.INTERACTIVE_ROUND_LIMIT', limit + 2), patch('src.clean_agent_preview.httpx.AsyncClient', DiagnosticClient):
            async with asyncio.timeout(240):
                async for chunk in stream_preview(
                    endpoint_url=os.environ["ENDPOINT_URL"],
                    model='odysseus-qwen3.5-tools-pre-heretic', headers={}, turn_contract=contract,
                    messages=[{'role': 'user', 'content': prompt}],
                    session_id=session, owner='sft_alex_creator', disabled_tools=set(), tool_policy=ToolPolicy(),
                ):
                    if '[DONE]' in chunk:
                        continue
                    event = json.loads(chunk[6:])
                    if event.get('type') in {'tool_start', 'tool_output', 'final_response', 'completion_recovery', 'error'}:
                        bounded = {k: event[k] for k in ('type', 'tool', 'round', 'command', 'error', 'exit_code', 'reason', 'content') if k in event}
                        if event.get('type') == 'tool_output':
                            bounded['output'] = str(event.get('output', ''))[:1800]
                        row['events'].append(bounded)
                    if isinstance(event.get('delta'), str):
                        row['streamed_text'] = (row.get('streamed_text', '') + event['delta'])[-2400:]
    except Exception as exc:
        row['error_type'] = type(exc).__name__
    finally:
        closed = await PrivateBrowserTool().execute(json.dumps({'action': 'close'}), {'session_id': session})
        row['cleanup'] = closed.get('exit_code') == 0 and not closed.get('error')
        row['seconds'] = round(time.monotonic() - start, 2)
    row['executions'] = sum(event['type'] == 'tool_start' for event in row['events'])
    row['final'] = '\n'.join(event.get('content', '') for event in row['events']
                             if event['type'] == 'final_response') or row.get('streamed_text', '')
    row['semantic_review'] = 'pending; final claims must be checked against observed product evidence'
    return row


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limits', nargs='+', type=int, choices=(6, 10), default=[6, 10])
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')
    path = Path(__file__).resolve().parents[1] / 'reports' / f'browser-budget-probe-{stamp}.json'
    if path.exists():
        raise FileExistsError(path)
    report = {'scope': 'Isolated stream_preview and real browser; not a real UI replay or a randomized performance benchmark.', 'arms': []}
    for limit in args.limits:
        row = await probe(limit)
        report['arms'].append(row)
        with path.open('w') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print(json.dumps({'limit': limit, 'executions': row['executions'], 'seconds': row['seconds'],
                          'cleanup': row['cleanup'], 'final': row['final'], 'error_type': row.get('error_type'),
                          'provider_errors': row.get('provider_errors', [])}), flush=True)
    print(str(path), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
