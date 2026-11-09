"""Isolated real-model probe through stream_preview; all tool data is synthetic.

No UI configuration changes or real tool dispatch. Records only public prompts,
chosen tool arguments, counters and bounded final answers; no request headers.
"""
import os
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.clean_agent_preview import stream_preview
from src.agent_tools.web_tools import WebSearchTool
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract
from src.tool_policy import ToolPolicy


async def probe(prompt):
    queries, calls, events = [], [], []
    def provider(query, **kwargs):
        queries.append(query)
        if len(queries) == 1:
            return 'No search results found. All providers returned empty; retrying or inspecting a known source may help.', []
        return 'Synthetic search fixture: IANA-managed Reserved Domains.', [
            {'title': 'IANA-managed Reserved Domains', 'url': 'https://www.iana.org/domains/reserved'}]

    async def execute(block, **kwargs):
        # Legacy tool blocks can transport a search query as plain text.
        try:
            args = json.loads(block.content)
        except ValueError:
            args = {'query' if block.tool_type == 'web_search' else 'url': block.content}
        calls.append({'tool': block.tool_type, 'arguments': args})
        if block.tool_type == 'web_search':
            return 'fixture search', await WebSearchTool().execute(block.content, {})
        if block.tool_type == 'web_fetch':
            return 'fixture fetch', {'output': 'Synthetic page fixture: IANA manages reserved domains for documentation and testing.', 'exit_code': 0}
        raise AssertionError('Unexpected tool reached isolated fixture dispatcher')

    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'web_search', 'web_fetch'}]
    contract = replace(resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
                       routing_experiment='recent_model_choice')
    with patch('src.clean_agent_preview.execute_tool_block', execute), patch('src.search.comprehensive_web_search', provider):
        async with asyncio.timeout(100):
            async for chunk in stream_preview(
                endpoint_url=os.environ["ENDPOINT_URL"],
                model='odysseus-qwen3.5-tools-pre-heretic', headers={}, turn_contract=contract,
                messages=[{'role': 'user', 'content': prompt}], session_id='isolated-search-probe',
                owner='isolated-search-probe', disabled_tools=set(), tool_policy=ToolPolicy(),
            ):
                if '[DONE]' not in chunk:
                    events.append(json.loads(chunk[6:]))
    return {'prompt': prompt, 'calls': calls, 'search_count': len(queries),
            'valid_probe': bool(queries),
            'outputs': [{k: e.get(k) for k in ('tool', 'error', 'evidence_status')}
                        for e in events if e.get('type') == 'tool_output'],
            'final': ('\n'.join(e.get('content', '') for e in events
                               if e.get('type') == 'final_response')
                      or ''.join(e.get('delta', '') for e in events
                                 if isinstance(e.get('delta'), str)))[:1200],
            'fixture_evidence_reached': len(queries) > 1 or any(c['tool'] == 'web_fetch' for c in calls)}


async def main():
    report = {'scope': 'Real served model and stream_preview; synthetic tool boundary, not a real UI or provider benchmark.', 'cases': []}
    for prompt in [
        'Search for the official IANA reserved domains page. Return the source.',
        'Search for the official IANA reserved domains page. If no results come back, retry that same search once.',
    ]:
        try:
            result = await probe(prompt)
        except Exception as exc:
            result = {'prompt': prompt, 'error_type': type(exc).__name__}
        report['cases'].append(result)
        print(json.dumps(result), flush=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')
    path = Path(__file__).resolve().parents[1] / 'reports' / f'empty-search-model-probe-{timestamp}.json'
    with path.open('x') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
    print(str(path), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
