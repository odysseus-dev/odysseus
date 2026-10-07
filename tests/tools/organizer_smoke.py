"""Live model + real organizer handlers with disposable SQLite storage.

No scheduler runs; calendar fixtures are local, with no external sync.

Run with PYTHONPATH=. ODYSSEUS_EDITOR_TEST_ENDPOINT=<chat completions URL>.
"""
import asyncio
import json
import os
import re
import tempfile
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core import database
from src.tools.notes import do_manage_notes
from src.tools.system import do_manage_tasks
from src.tools.calendar import do_manage_calendar

import jsonschema

from src import clean_agent_preview as runner
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import requested_capabilities, selected_tools_for_request, resolve_turn_contract


CASES = [
    ('checklist', 'Make a todo list: buy milk, return the keys, call Sam.', 'manage_notes'),
    ('plain-note', 'Save a note titled Project ideas with the text: Build a small weather display.', 'manage_notes'),
    ('daily', 'Create a task to summarize technology news every day at 07:30 UTC.', 'manage_tasks'),
    ('weekly', 'Every Monday at 09:00 UTC research new battery technology for me.', 'manage_tasks'),
    ('calendar', 'Add a calendar event named Design review on October 5, 2026 at 14:00 UTC for one hour.', 'manage_calendar'),
    ('calendar-all-day', 'Add an all-day calendar event named Office closed on October 8, 2026. Only that day.', 'manage_calendar'),
    ('calendar-offset', 'Add a calendar event named Tokyo call on October 6, 2026 at 18:00 Japan time (UTC+09:00) for 30 minutes.', 'manage_calendar'),
    ('calendar-tokyo-simple', 'Add a calendar event named Tokyo call on October 6, 2026 at 6pm Tokyo time for 30 minutes.', 'manage_calendar'),
    ('calendar-half-offset', 'Create a calendar event called Early call on October 6, 2026 at 00:15 UTC+05:30, lasting 30 minutes.', 'manage_calendar'),
    ('calendar-negative', 'Create a calendar event called Late call on October 6, 2026 at 22:00 UTC-04:00, lasting 30 minutes.', 'manage_calendar'),
    ('calendar-dst', 'Add Summer call to my calendar on July 6, 2027 at 10am America/New_York time for 30 minutes.', 'manage_calendar'),
    ('calendar-move', 'Move it to 16:30 UTC on the same day. Keep its duration.', 'manage_calendar'),
    ('calendar-rename', 'Rename it to Planning review. Keep its date and time unchanged.', 'manage_calendar'),
    ('calendar-shorten', 'Make that event 30 minutes long. Keep its start time and title.', 'manage_calendar'),
    ('calendar-midnight', 'Move that event to October 5, 2026 at 23:30 UTC. Keep its one-hour duration and title.', 'manage_calendar'),
    ('calendar-move-zone', 'Move that event to October 6, 2026 at 18:00 Tokyo time. Keep its one-hour duration and title.', 'manage_calendar'),
    ('calendar-relative-hours', 'Push that event back by two hours.', 'manage_calendar'),
    ('calendar-relative-day', 'Move that event to the following day at the same time.', 'manage_calendar'),
    ('calendar-hypothetical', 'What time would that event start if it were pushed back by two hours? Do not change it.', 'manage_calendar'),
    ('calendar-hypothetical-later', 'What time would that event start if it began two hours later? Do not change it.', 'manage_calendar'),
    ('calendar-hypothetical-earlier', 'What time would that event start if it began two hours earlier? Do not change it.', 'manage_calendar'),
    ('calendar-no-change', 'Do not move that event. Just remind me of its current start time.', 'manage_calendar'),
    ('calendar-wording', 'Suggest a short message asking to postpone that event by two hours. Do not modify my calendar.', 'manage_calendar'),
    ('note-append', 'Add "Check the battery life." to the end of that note. Keep what is already there.', 'manage_notes'),
    ('checklist-remove', 'Remove return the keys from that checklist. Leave the other items and their checked states alone.', 'manage_notes'),
    ('checklist-check', 'Mark call Sam as done in that checklist. Keep everything else.', 'manage_notes'),
    ('checklist-rename', 'Rename that checklist to Weekend errands. Keep all items and their checked states.', 'manage_notes'),
    ('checklist-uncheck', 'Uncheck buy milk in that checklist. Keep the other items unchanged.', 'manage_notes'),
    ('checklist-uncheck-last', 'Uncheck call Sam in that checklist. Keep the other items unchanged.', 'manage_notes'),
    ('checklist-reorder', 'Move call Sam to the top of that checklist. Keep the other items in order and preserve their checked states.', 'manage_notes'),
    ('checklist-append', 'Add pick up parcel at the end of that checklist, unchecked. Keep the existing items and their checked states.', 'manage_notes'),
    ('checklist-unfinished', 'List only the unfinished items in that checklist. Do not change anything.', 'manage_notes'),
    ('checklist-finished', 'Which items in that checklist are already done? List only those. Do not change anything.', 'manage_notes'),
    ('task-pause', 'Pause that task.', 'manage_tasks'),
    ('task-retime', 'Change that task to 08:45 UTC every day. Keep its name and prompt.', 'manage_tasks'),
    ('task-rename', 'Rename that task to Morning briefing. Keep everything else.', 'manage_tasks'),
    ('task-offset', 'Create a task to summarize technology news every day at 07:30 UTC+09:00.', 'manage_tasks'),
    ('task-weekdays', 'Create one task to summarize technology news every Monday, Wednesday and Friday at 09:15 UTC.', 'manage_tasks'),
    ('task-monthly', 'Create a task to review my budget on the 15th of every month at 08:00 UTC.', 'manage_tasks'),
    ('task-cron-retime', 'Move that task to 10:30 UTC. Keep the same weekdays.', 'manage_tasks'),
    ('task-cron-days', 'Run that task on Tuesdays and Thursdays instead. Keep the same time.', 'manage_tasks'),
    ('task-cron-pause', 'Pause that task.', 'manage_tasks'),
    ('task-cron-resume', 'Resume that task with its existing schedule.', 'manage_tasks'),
    ('task-monthly-day', 'Move that task to the 20th of each month. Keep the same time and instructions.', 'manage_tasks'),
    ('task-monthly-time', 'Run that task at 11:45 UTC instead. Keep the same day of the month and instructions.', 'manage_tasks'),
]

CALENDAR_EXPECTED = {
    'calendar-offset': ('Tokyo call', '2026-10-06T09:00:00', '2026-10-06T09:30:00'),
    'calendar-tokyo-simple': ('Tokyo call', '2026-10-06T09:00:00', '2026-10-06T09:30:00'),
    'calendar-half-offset': ('Early call', '2026-10-05T18:45:00', '2026-10-05T19:15:00'),
    'calendar-negative': ('Late call', '2026-10-07T02:00:00', '2026-10-07T02:30:00'),
    'calendar-dst': ('Summer call', '2027-07-06T14:00:00', '2027-07-06T14:30:00'),
    'calendar-move': ('Design review', '2026-10-05T16:30:00', '2026-10-05T17:30:00'),
    'calendar-rename': ('Planning review', '2026-10-05T14:00:00', '2026-10-05T15:00:00'),
    'calendar-shorten': ('Design review', '2026-10-05T14:00:00', '2026-10-05T14:30:00'),
    'calendar-midnight': ('Design review', '2026-10-05T23:30:00', '2026-10-06T00:30:00'),
    'calendar-move-zone': ('Design review', '2026-10-06T09:00:00', '2026-10-06T10:00:00'),
    'calendar-relative-hours': ('Design review', '2026-10-05T16:00:00', '2026-10-05T17:00:00'),
    'calendar-relative-day': ('Design review', '2026-10-06T14:00:00', '2026-10-06T15:00:00'),
    'calendar-hypothetical': ('Design review', '2026-10-05T14:00:00', '2026-10-05T15:00:00'),
    'calendar-hypothetical-later': ('Design review', '2026-10-05T14:00:00', '2026-10-05T15:00:00'),
    'calendar-hypothetical-earlier': ('Design review', '2026-10-05T14:00:00', '2026-10-05T15:00:00'),
    'calendar-no-change': ('Design review', '2026-10-05T14:00:00', '2026-10-05T15:00:00'),
    'calendar-wording': ('Design review', '2026-10-05T14:00:00', '2026-10-05T15:00:00'),
}


async def seed_followup(case, owner):
    if case in {'calendar-move', 'calendar-rename', 'calendar-shorten', 'calendar-midnight', 'calendar-move-zone', 'calendar-relative-hours', 'calendar-relative-day', 'calendar-no-change', 'calendar-wording'} or case.startswith('calendar-hypothetical'):
        tool = 'manage_calendar'
        prompt = 'Create Design review on my calendar, October 5, 2026, 14:00 UTC for one hour.'
        args = dict(action='create_event', summary='Design review', dtstart='2026-10-05T14:00:00Z', dtend='2026-10-05T15:00:00Z')
        result = await do_manage_calendar(json.dumps(args), owner=owner)
    elif case == 'note-append':
        tool = 'manage_notes'
        prompt = 'Save a note titled Project ideas with the text: Build a small weather display.'
        args = dict(action='add', title='Project ideas', content='Build a small weather display.')
        result = await do_manage_notes(json.dumps(args), owner=owner)
    elif case.startswith('checklist-'):
        tool = 'manage_notes'
        prompt = 'Save a checklist called Errands: buy milk (done), return the keys, call Sam.'
        args = dict(action='add', title='Errands', note_type='checklist', checklist_items=[
            {'text': 'buy milk', 'done': True}, {'text': 'return the keys', 'done': False},
            {'text': 'call Sam', 'done': False}])
        if case == 'checklist-uncheck-last':
            prompt = 'Save a checklist called Errands: buy milk (done), return the keys, call Sam (done).'
            args['checklist_items'][2]['done'] = True
        result = await do_manage_notes(json.dumps(args), owner=owner)
    elif case in {'task-monthly-day', 'task-monthly-time'}:
        tool = 'manage_tasks'
        prompt = 'Create a task called Budget review to review my budget on the 15th of each month at 08:00 UTC.'
        args = dict(action='create', name='Budget review', prompt='Review my budget.',
                    day_of_month=15, scheduled_time='08:00', task_type='llm')
        result = await do_manage_tasks(json.dumps(args), owner=owner)
    elif case.startswith('task-cron-'):
        tool = 'manage_tasks'
        prompt = 'Create one task called Tech digest to summarize technology news every Monday, Wednesday and Friday at 09:15 UTC.'
        args = dict(action='create', name='Tech digest', prompt='Summarize technology news.',
                    weekdays=['monday', 'wednesday', 'friday'], scheduled_time='09:15', task_type='llm')
        result = await do_manage_tasks(json.dumps(args), owner=owner)
    elif case in {'task-pause', 'task-retime', 'task-rename'}:
        tool = 'manage_tasks'
        prompt = 'Create a task called Tech digest to summarize technology news every day at 07:30 UTC.'
        args = dict(action='create', name='Tech digest', prompt='Summarize technology news.',
                    schedule='daily', scheduled_time='07:30', task_type='llm')
        result = await do_manage_tasks(json.dumps(args), owner=owner)
    else:
        return [], None
    assert result.get('exit_code', 0) == 0, result
    history = [{'role': 'user', 'content': prompt}, {'role': 'assistant',
        'content': result['response'], 'metadata': {
            'tool_events': [{'tool': tool, 'exit_code': 0}],
            'clean_v3_turn': [
                {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'seed1', 'type': 'function',
                    'function': {'name': tool, 'arguments': json.dumps(args)}}]},
                {'role': 'tool', 'tool_call_id': 'seed1', 'content': json.dumps(result)},
            ],
        }}]
    if case == 'task-cron-resume':
        paused = await do_manage_tasks(json.dumps({'action': 'pause', 'task_id': result['task_id']}), owner=owner)
        assert paused.get('exit_code') == 0, paused
        history.extend([{'role': 'user', 'content': 'Pause that task.'},
                        {'role': 'assistant', 'content': paused['response']}])
    return history, result.get('uid') or result.get('note_id') or result.get('task_id')


def validate_case(case, args):
    if case == 'checklist':
        assert args['action'] == 'add' and args['note_type'] == 'checklist'
        texts = [item['text'].lower() for item in args['checklist_items']]
        assert len(texts) == 3
        assert all(any(word in text for text in texts) for word in ('milk', 'keys', 'sam'))
        assert all(not item.get('done') for item in args['checklist_items'])
    elif case == 'plain-note':
        assert args['action'] == 'add' and args['title'] == 'Project ideas'
        assert 'weather display' in args.get('content', '').lower()
    elif case in {'daily', 'weekly'}:
        assert args['action'] == 'create'
        assert args.get('name') and args.get('prompt')
        assert args['schedule'] == case
        assert args['scheduled_time'] == ('07:30' if case == 'daily' else '09:00')
        if case == 'weekly': assert args['scheduled_day'] == 0
    else:
        assert args['action'] == 'create_event'
        assert args['summary'] == 'Design review'
        assert args['dtstart'].startswith('2026-10-05T14:00')
        assert args['dtend'].startswith('2026-10-05T15:00')


def visible_answer(events):
    answer = ''
    for item in events:
        if item.get('type') == 'final_response':
            answer = item.get('content') or ''
        elif isinstance(item.get('delta'), str):
            if item.get('replacement_scope') == 'turn':
                answer = ''
            answer += item['delta']
    return answer


async def main():
    if os.environ.get('ODYSSEUS_ORGANIZER_TRACE'):
        original_choice = runner.provider_compatible_tool_choice_request
        original_lines = runner.preview_lines_until_finish

        def capture_request(request, model):
            request = original_choice(request, model)
            if os.environ.get('ODYSSEUS_ORGANIZER_AUTO_CHOICE') and request.get('tools'):
                request = {**request, 'tool_choice': 'auto'}
            print('WIRE_REQUEST', json.dumps({key: request.get(key) for key in
                ('model', 'tool_choice', 'tools', 'max_tokens', 'stop')}), flush=True)
            if os.environ.get('ODYSSEUS_ORGANIZER_TRACE_MESSAGES'):
                print('WIRE_MESSAGES', json.dumps(request.get('messages', [])), flush=True)
            return request

        async def capture_lines(response, finish_event):
            arguments = {}
            async for line in original_lines(response, finish_event):
                if line.startswith('data: ') and line[6:] != '[DONE]':
                    payload = json.loads(line[6:])
                    for choice in payload.get('choices', []):
                        for call in choice.get('delta', {}).get('tool_calls', []):
                            index = call['index']
                            arguments[index] = arguments.get(index, '') + call.get('function', {}).get('arguments', '')
                yield line
            print('WIRE_ARGUMENTS', json.dumps(arguments), flush=True)

        runner.provider_compatible_tool_choice_request = capture_request
        runner.preview_lines_until_finish = capture_lines
    failures = []
    for case, prompt, expected in CASES:
        if os.environ.get('ODYSSEUS_ORGANIZER_CASES') and case not in os.environ['ODYSSEUS_ORGANIZER_CASES'].split(','):
            continue
        calls = []
        owner = 'fixture-' + case
        history, original_id = await seed_followup(case, owner)
        async def execute(block, **kwargs):
            print('PROPOSED', case, block.tool_type, block.content[:2000], flush=True)
            if block.tool_type != expected:
                return block.tool_type, {'exit_code': 1, 'error': 'This recording fixture only implements the requested organizer tool.'}
            args = json.loads(block.content)
            schema = next(s['function']['parameters'] for s in FUNCTION_TOOL_SCHEMAS
                          if s['function']['name'] == expected)
            jsonschema.validate(args, schema)
            calls.append(args)
            if expected == 'manage_notes':
                return expected, await do_manage_notes(block.content, owner=owner)
            if expected == 'manage_tasks':
                return expected, await do_manage_tasks(block.content, owner=owner)
            return expected, await do_manage_calendar(block.content, owner=owner)

        runner.execute_tool_block = execute
        policy = ToolPolicy()
        contract = resolve_turn_contract(
            capabilities=requested_capabilities(prompt, history), schemas=FUNCTION_TOOL_SCHEMAS,
            policy=policy, selected_tools=selected_tools_for_request(prompt),
            required_tools=selected_tools_for_request(prompt) or (), message=prompt, history=history,
        )
        events = []
        try:
            async for chunk in runner.stream_preview(
                endpoint_url=os.environ['ODYSSEUS_EDITOR_TEST_ENDPOINT'], model='Ajax', headers={},
                messages=[{'role': 'user', 'content': prompt}], turn_contract=contract,
                history_session=SimpleNamespace(history=history),
                session_id='fixture-organizer-' + case, owner=owner,
                disabled_tools=set(), tool_policy=policy, thinking_mode='off', max_rounds=4,
            ):
                if chunk.startswith('data: ') and '[DONE]' not in chunk:
                    events.append(json.loads(chunk[6:]))
            answer = visible_answer(events)
            if case in {'checklist-unfinished', 'checklist-finished'}:
                assert all(args['action'] in {'view', 'list', 'search'} for args in calls), calls
                expected_words = ('keys', 'sam') if case == 'checklist-unfinished' else ('milk',)
                excluded_words = ('milk',) if case == 'checklist-unfinished' else ('keys', 'sam')
                assert all(word in answer.lower() for word in expected_words), answer
                assert not any(word in answer.lower() for word in excluded_words), answer
            if case in {'calendar-no-change', 'calendar-wording'} or case.startswith('calendar-hypothetical'):
                assert all(args['action'] in {'list_events', 'list_calendars'} for args in calls), calls
                assert answer.strip(), f'Empty answer; events: {events}'
                assert 'Open the document you want reviewed' not in answer, answer
                if case.startswith('calendar-hypothetical'):
                    pattern = (r'\b(?:12:00|12(?::00)?\s*p\.?m\.?|noon)\b'
                               if case == 'calendar-hypothetical-earlier'
                               else r'\b(?:16:00|4(?::00)?\s*p\.?m\.?)\b')
                    assert re.search(pattern, answer, re.I), answer
            with database.SessionLocal() as db:
                if expected == 'manage_notes':
                    rows = db.query(database.Note).filter_by(owner=owner).all()
                    assert len(rows) == 1, f'Expected one note, got {len(rows)}'
                    row = rows[0]
                    if case == 'note-append':
                        assert row.id == original_id
                        assert row.title == 'Project ideas'
                        assert 'Build a small weather display.' in row.content
                        assert row.content.rstrip().endswith('Check the battery life.')
                    elif case.startswith('checklist-'):
                        assert row.id == original_id
                        assert row.title == ('Weekend errands' if case == 'checklist-rename' else 'Errands')
                        assert row.note_type == 'checklist'
                        wanted = [{'text': 'buy milk', 'done': case != 'checklist-uncheck'}]
                        if case != 'checklist-remove':
                            wanted.append({'text': 'return the keys', 'done': False})
                        wanted.append({'text': 'call Sam', 'done': case == 'checklist-check'})
                        if case == 'checklist-reorder':
                            wanted.insert(0, wanted.pop())
                        elif case == 'checklist-append':
                            wanted.append({'text': 'pick up parcel', 'done': False})
                        assert json.loads(row.items) == wanted, f'Expected {wanted}, got {row.items}'
                    else:
                        validate_case(case, {'action': 'add', 'title': row.title, 'content': row.content or '',
                                             'note_type': row.note_type, 'checklist_items': json.loads(row.items or '[]')})
                elif expected == 'manage_tasks':
                    rows = db.query(database.ScheduledTask).filter_by(owner=owner).all()
                    assert len(rows) == 1, f'Expected one task, got {len(rows)}'
                    row = rows[0]
                    if case.startswith('task-cron-'):
                        from croniter import croniter
                        from datetime import datetime
                        assert row.id == original_id and row.name == 'Tech digest'
                        assert row.prompt == 'Summarize technology news.'
                        assert row.status == ('paused' if case == 'task-cron-pause' else 'active')
                        cron = croniter(row.cron_expression, datetime(2026, 10, 4))
                        days = (6, 8, 13) if case == 'task-cron-days' else (5, 7, 9)
                        hour, minute = (10, 30) if case == 'task-cron-retime' else (9, 15)
                        assert [cron.get_next(datetime) for _ in range(3)] == [
                            datetime(2026, 10, day, hour, minute) for day in days]
                        assert row.next_run is not None
                    elif case in {'task-pause', 'task-retime', 'task-rename'}:
                        assert row.id == original_id
                        assert row.name == ('Morning briefing' if case == 'task-rename' else 'Tech digest')
                        assert row.prompt == 'Summarize technology news.'
                        assert row.schedule == 'daily'
                        assert row.scheduled_time == ('08:45' if case == 'task-retime' else '07:30')
                        assert row.status == ('paused' if case == 'task-pause' else 'active')
                    elif case == 'task-offset':
                        assert row.schedule == 'daily' and row.scheduled_time == '22:30'
                        assert 'technology' in row.prompt.lower()
                    elif case == 'task-weekdays':
                        from croniter import croniter
                        from datetime import datetime
                        assert row.schedule == 'cron' and row.cron_expression
                        cron = croniter(row.cron_expression, datetime(2026, 10, 4, 0, 0))
                        assert [cron.get_next(datetime) for _ in range(3)] == [
                            datetime(2026, 10, 5, 9, 15), datetime(2026, 10, 7, 9, 15),
                            datetime(2026, 10, 9, 9, 15)]
                        assert row.next_run is not None
                    elif case in {'weekly', 'task-monthly', 'task-monthly-day', 'task-monthly-time'}:
                        from datetime import datetime
                        from src.task_scheduler import compute_next_run
                        after = datetime(2026, 10, 4)
                        actual = []
                        for _ in range(3):
                            after = compute_next_run(row.schedule, row.scheduled_time,
                                row.scheduled_day, after=after, cron_expression=row.cron_expression)
                            actual.append(after)
                        wanted = ([datetime(2026, 10, day, 9) for day in (5, 12, 19)] if case == 'weekly'
                                  else [datetime(2026, month, 15, 8) for month in (10, 11, 12)])
                        if original_id:
                            assert row.id == original_id and row.name == 'Budget review'
                            assert row.prompt == 'Review my budget.' and row.status == 'active'
                            day = 20 if case == 'task-monthly-day' else 15
                            hour, minute = (11, 45) if case == 'task-monthly-time' else (8, 0)
                            wanted = [datetime(2026, month, day, hour, minute) for month in (10, 11, 12)]
                        assert actual == wanted, f'Expected {wanted}, got {actual}'
                    else:
                        validate_case(case, {'action': 'create', 'name': row.name, 'prompt': row.prompt,
                            'schedule': row.schedule, 'scheduled_time': row.scheduled_time, 'scheduled_day': row.scheduled_day})
                else:
                    rows = db.query(database.CalendarEvent).join(database.CalendarCal).filter(
                        database.CalendarCal.owner == owner).all()
                    assert len(rows) == 1, f'Expected one calendar event, got {len(rows)}'
                    row = rows[0]
                    if original_id:
                        assert row.uid == original_id, 'Follow-up must update the existing event'
                    print('SAVED_CALENDAR', json.dumps({'summary': row.summary,
                        'start': row.dtstart.isoformat(), 'end': row.dtend.isoformat(),
                        'is_utc': row.is_utc, 'all_day': row.all_day}), flush=True)
                    assert not row.rrule, 'One-off event unexpectedly recurs'
                    assert not row.caldav_sync_pending, 'Fixture must not sync externally'
                    if case == 'calendar-all-day':
                        assert row.summary == 'Office closed' and row.all_day
                        assert row.dtstart.isoformat() == '2026-10-08T00:00:00'
                        assert row.dtend.isoformat() == '2026-10-09T00:00:00'
                    elif case in CALENDAR_EXPECTED:
                        title, start, end = CALENDAR_EXPECTED[case]
                        assert row.summary == title and row.is_utc and not row.all_day
                        assert row.dtstart.isoformat() == start, f'Expected UTC start {start}, got {row.dtstart}'
                        assert row.dtend.isoformat() == end, f'Expected UTC end {end}, got {row.dtend}'
                    else:
                        assert row.is_utc and not row.all_day
                        validate_case(case, {'action': 'create_event', 'summary': row.summary,
                            'dtstart': row.dtstart.isoformat(), 'dtend': row.dtend.isoformat()})
            passed = True
        except Exception as exc:
            passed = False
            failures.append(case)
            print(case, type(exc).__name__, str(exc), flush=True)
        outputs = [e for e in events if e.get('type') == 'tool_output']
        print(json.dumps({'case': case, 'passed': passed, 'calls': calls,
                          'outputs': outputs, 'answer': visible_answer(events)}, ensure_ascii=False), flush=True)
    if failures:
        raise SystemExit('Failed: ' + ', '.join(failures))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='organizer-fixture-') as tmp:
        engine = create_engine('sqlite:///' + tmp + '/test.db')
        database.Base.metadata.create_all(engine)
        database.SessionLocal = sessionmaker(bind=engine)
        try:
            asyncio.run(main())
        finally:
            engine.dispose()
