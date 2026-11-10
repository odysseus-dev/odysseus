"""Non-mutating Ajax first-call smoke test; records proposals, never executes tools."""
import argparse
import json
import time
from pathlib import Path

import httpx
import jsonschema

from src.clean_agent_preview import compact_schemas
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


CASES = [
    ('todo', 'Make a todo: drop keys, drop off Bjorn, buy a present.', 'manage_notes'),
    ('note', 'Save a note titled Door code with body: Ask the concierge.', 'manage_notes'),
    ('notes_lookup', 'Find my note about the dentist.', 'manage_notes'),
    ('calendar_today', 'Add a calendar meeting today at 2pm.', 'manage_calendar'),
    ('calendar_ambiguous', 'Add calendar meeting 2pm.', 'manage_calendar'),
    ('calendar_list', 'What is on my calendar tomorrow?', 'manage_calendar'),
    ('task_daily', 'Every day at 7:30am summarize my unread emails in a chat.', 'manage_tasks'),
    ('task_list', 'Show my paused tasks.', 'manage_tasks'),
    ('document', 'Create a Python document that prints hello world.', 'create_document'),
    ('search', 'Search the web for the latest Blender release.', 'web_search'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows = []
    core = {'bash', 'python', 'read_file', 'web_fetch', 'web_search'}
    with httpx.Client(timeout=90) as client:
        for name, prompt, expected in CASES:
            tools = compact_schemas([s for s in FUNCTION_TOOL_SCHEMAS
                                     if s['function']['name'] in core | {expected}], model='Ajax')
            start = time.monotonic()
            response = client.post(args.endpoint.rstrip('/') + '/chat/completions', json={
                'model': 'Ajax', 'temperature': 0, 'max_tokens': 768,
                'chat_template_kwargs': {'enable_thinking': False}, 'tools': tools,
                'messages': [
                    {'role': 'system', 'content': 'You are an assistant using Odysseus tools. '
                     'Current local date/time: 2026-09-30 09:00, UTC+02:00. '
                     'Current UTC date/time: 2026-09-30 07:00. No document is open. '
                     'Use tools to fulfill requests, and ask in plain text when required information is missing.'},
                    {'role': 'user', 'content': prompt},
                ],
            })
            response.raise_for_status()
            message = response.json()['choices'][0]['message']
            calls = message.get('tool_calls') or []
            errors = []
            for call in calls:
                try:
                    fn = call['function']
                    schema = next(s['function']['parameters'] for s in tools if s['function']['name'] == fn['name'])
                    jsonschema.validate(json.loads(fn['arguments']), schema)
                except (ValueError, StopIteration, jsonschema.ValidationError) as exc:
                    errors.append(str(exc)[:250])
            row = {'case': name, 'prompt': prompt, 'expected_tool': expected,
                   'seconds': round(time.monotonic() - start, 3), 'message': message,
                   'schema_errors': errors}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            Path(args.output).write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
