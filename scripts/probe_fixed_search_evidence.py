"""Real model + canonical runtime; fixed fictional search evidence, no web writes.

Diagnostic only: these results are not a real-web benchmark score. Every tool
execution is intercepted in this process, so no public/example URL is fetched.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.clean_agent_preview as runtime
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract

SOURCES = {
    'https://example.org/alder': ('Alder fictional logger specification',
        'Alder costs $37 per device. It connects through USB only; it does not support Wi-Fi. '
        'The manufacturer advertises 40 hours of battery life. An independent test measured 26 hours. '
        'Both values refer to the same device; advertising is not a measured result.'),
    'https://example.org/birch': ('Birch fictional logger specification',
        'Birch costs $52 per device and supports Wi-Fi and USB. The manufacturer advertises '
        '32 hours of battery life; the same independent test measured 29 hours. '
        'No shipping cost or warranty duration was supplied for either product.'),
}
REPORT = '```sources\n' + '\n'.join(f'[{i}] {title}\n    {url}' for i, (url, (title, _)) in enumerate(SOURCES.items(), 1)) + '\n```\nQuery: fictional logger specifications\n'
REPORT += '\n'.join(f'\n[CONTENT {i}] From: {url}\nTitle: {title}\n------------------------------\n{body}' for i, (url, (title, body)) in enumerate(SOURCES.items(), 1))
CASES = [
    ('comparison', 'Search for the fictional Alder and Birch logger specifications. Compare price, Wi-Fi support and measured battery life. How much more does Birch cost? Cite sources.'),
    ('comparison-typo', 'serch alder vs birch loggers, price diffrence wifi and tested battry life? sources pls'),
    ('evidence-boundary', 'Look up the fictional Alder and Birch loggers. Which lasts longer in the independent test, and is that the same ranking as the advertised battery life? What are their warranty durations? Cite sources.'),
]

async def execute(block, **kwargs):
    if block.tool_type == 'web_search':
        return 'fixed search', {'output': REPORT, 'exit_code': 0, 'evidence_status': 'available'}
    if block.tool_type == 'web_fetch':
        args = json.loads(block.content)
        url = args.get('url')
        if url in SOURCES:
            title, body = SOURCES[url]
            return 'fixed fetch', {'output': f'# {title}\nSource: {url}\n{body}', 'exit_code': 0}
    return 'fixed fixture', {'error': 'No fixture for this tool or URL. Do not invent evidence.', 'exit_code': 1}

async def main():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'web_search', 'web_fetch'}]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    original = runtime.execute_tool_block
    runtime.execute_tool_block = execute
    results = []
    path = Path('reports') / ('fixed-search-evidence-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
    path.parent.mkdir(exist_ok=True)
    try:
        for name, prompt in CASES:
            started = time.monotonic()
            events = []
            async for chunk in runtime.stream_preview(
                endpoint_url=os.environ["ENDPOINT_URL"],
                model=os.environ.get('MODEL', 'model-f'), headers={},
                messages=[{'role': 'user', 'content': prompt}], turn_contract=contract,
                session_id='fixed-search-evidence', owner='sft_alex_creator',
                disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=8,
            ):
                if chunk.startswith('data: ') and '[DONE]' not in chunk:
                    events.append(json.loads(chunk[6:]))
            finals = [e['content'] for e in events if e.get('type') == 'final_response']
            answer = finals[-1] if finals else ''.join(e.get('delta', '') for e in events)
            result = {'name': name, 'prompt': prompt, 'seconds': time.monotonic()-started, 'answer': answer, 'events': events}
            results.append(result)
            path.write_text(json.dumps({'fixture_sources': SOURCES, 'diagnostic_only': True, 'results': results}, indent=2) + '\n')
            print(json.dumps({k:v for k,v in result.items() if k != 'events'}), flush=True)
    finally:
        runtime.execute_tool_block = original
    print(path, flush=True)

if __name__ == '__main__':
    asyncio.run(main())
