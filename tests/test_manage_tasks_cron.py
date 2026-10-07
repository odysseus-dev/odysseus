import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from src.tools.system import do_manage_tasks


@pytest.fixture
def storage(monkeypatch, tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'tasks.db'))
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr('src.task_scheduler._utcnow', lambda: datetime(2026, 10, 4))
    yield factory
    engine.dispose()


async def call(**args):
    return await do_manage_tasks(json.dumps(args), owner='fixture')


@pytest.mark.asyncio
async def test_cron_create_edit_resume_and_invalid_edit_rollback(storage):
    result = await call(action='create', name='Digest', prompt='Summarize technology news.',
                        schedule='cron', cron_expression='15 9 * * 1,3,5')
    assert result['exit_code'] == 0, result
    task_id = result['task_id']

    def saved():
        with storage() as db:
            return db.get(database.ScheduledTask, task_id)

    assert saved().next_run == datetime(2026, 10, 5, 9, 15)
    assert saved().cron_expression == '15 9 * * 1,3,5'
    assert '15 9 * * 1,3,5' in (await call(action='list'))['response']
    edited = await call(action='edit', task_id=task_id, cron_expression='45 8 * * 2,4')
    assert edited['exit_code'] == 0, edited
    assert saved().next_run == datetime(2026, 10, 6, 8, 45)
    for action in ('pause', 'resume'):
        assert (await call(action=action, task_id=task_id))['exit_code'] == 0
    assert saved().next_run == datetime(2026, 10, 6, 8, 45)
    rejected = await call(action='edit', task_id=task_id, name='Must not stick', cron_expression='bad')
    assert rejected['exit_code'] == 1
    assert saved().name == 'Digest'
    assert saved().cron_expression == '45 8 * * 2,4'


@pytest.mark.asyncio
@pytest.mark.parametrize('expression', [None, '', 'bad', '99 99 * * *'])
async def test_invalid_cron_does_not_create_inert_task(storage, expression):
    result = await call(action='create', name='Invalid', prompt='Test', schedule='cron',
                        cron_expression=expression)
    assert result['exit_code'] == 1
    with storage() as db:
        assert db.query(database.ScheduledTask).count() == 0


def test_compact_schema_exposes_cron():
    from src.clean_agent_preview import compact_schemas
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_tasks')
    props = compact_schemas([schema], model='Ajax')[0]['function']['parameters']['properties']
    assert 'cron' in props['schedule']['enum']
    assert 'cron_expression' in props
    assert '0=Sunday' in props['cron_expression']['description']
    assert 'scheduled_day' not in props
    assert props['day_of_month']['minimum'] == 1
    assert props['day_of_month']['maximum'] == 31
    assert 'UTC' in props['scheduled_time']['description']
    assert 'weekdays' in props


@pytest.mark.asyncio
async def test_named_weekdays_create_and_edit_preserve_actual_clock(storage):
    created = await call(action='create', name='Digest', prompt='News',
                         weekdays=['monday', 'wednesday', 'friday'], scheduled_time='09:15')
    assert created['exit_code'] == 0, created
    with storage() as db:
        row = db.get(database.ScheduledTask, created['task_id'])
        assert row.schedule == 'cron'
        assert row.cron_expression == '15 9 * * 1,3,5'
    # Direct-cron tasks may have a different legacy scheduled_time field.
    raw = await call(action='create', name='Cron', prompt='News', schedule='cron',
                     cron_expression='45 8 * * 1,3,5')
    edited = await call(action='edit', task_id=raw['task_id'], weekdays=['tuesday', 'thursday'])
    assert edited['exit_code'] == 0, edited
    with storage() as db:
        row = db.get(database.ScheduledTask, raw['task_id'])
        assert row.cron_expression == '45 8 * * 2,4'
        assert row.next_run == datetime(2026, 10, 6, 8, 45)


@pytest.mark.asyncio
@pytest.mark.parametrize('extra', [
    {'weekdays': []}, {'weekdays': ['noday']}, {'weekdays': 'monday'},
    {'weekdays': ['monday'], 'scheduled_time': '25:00'},
    {'weekdays': ['monday'], 'scheduled_time': None},
    {'weekdays': ['monday'], 'cron_expression': '0 9 * * *'},
    {'weekdays': ['monday'], 'scheduled_day': 0},
    {'weekdays': ['monday'], 'trigger_type': 'event'},
    {'weekdays': ['monday'], 'schedule': 'once'},
])
async def test_invalid_weekday_schedule_is_not_saved(storage, extra):
    result = await call(**{'action': 'create', 'name': 'Invalid', 'prompt': 'Test',
                          'scheduled_time': '09:15', **extra})
    assert result['exit_code'] == 1
    with storage() as db:
        assert db.query(database.ScheduledTask).count() == 0


@pytest.mark.asyncio
async def test_month_day_create_and_edit(storage):
    result = await call(action='create', name='Monthly', prompt='Review budget',
                        day_of_month=15, scheduled_time='08:00')
    assert result['exit_code'] == 0, result
    with storage() as db:
        row = db.get(database.ScheduledTask, result['task_id'])
        assert row.schedule == 'monthly' and row.scheduled_day == 15
        assert row.next_run == datetime(2026, 10, 15, 8)
    assert (await call(action='edit', task_id=result['task_id'], day_of_month=20))['exit_code'] == 0
    with storage() as db:
        assert db.get(database.ScheduledTask, result['task_id']).next_run == datetime(2026, 10, 20, 8)


@pytest.mark.asyncio
@pytest.mark.parametrize('day', [0, 32, True, '15', None])
async def test_invalid_month_day_is_not_saved(storage, day):
    result = await call(action='create', name='Invalid', prompt='Test', day_of_month=day)
    assert result['exit_code'] == 1
    with storage() as db:
        assert db.query(database.ScheduledTask).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('schedule', ['daily', 'weekly', 'monthly', 'cron'])
async def test_recurring_schedule_does_not_silently_ignore_one_off_date(storage, schedule):
    result = await call(action='create', name='Invalid', prompt='Test', schedule=schedule,
                        scheduled_date='2099-01-15T08:00:00Z')
    assert result['exit_code'] == 1
    assert 'scheduled_date is only' in result['error']
    with storage() as db:
        assert db.query(database.ScheduledTask).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('expression', ['15 9 * * 1,3,5', '15 9 15 * *', '0,30 8-10 * * 2,4'])
async def test_time_only_edit_changes_cron_clock_not_calendar_fields(storage, expression):
    result = await call(action='create', name='Digest', prompt='News', schedule='cron',
                        cron_expression=expression)
    edited = await call(action='edit', task_id=result['task_id'], scheduled_time='10:30')
    assert edited['exit_code'] == 0, edited
    with storage() as db:
        row = db.get(database.ScheduledTask, result['task_id'])
        assert row.cron_expression.split() == ['30', '10', *expression.split()[2:]]
        assert row.next_run.hour == 10 and row.next_run.minute == 30
        assert row.name == 'Digest' and row.prompt == 'News'


@pytest.mark.asyncio
async def test_invalid_cron_retime_rolls_back_all_edits(storage):
    result = await call(action='create', name='Digest', prompt='News', schedule='cron',
                        cron_expression='15 9 * * 1,3,5')
    edited = await call(action='edit', task_id=result['task_id'], scheduled_time='25:00', name='Wrong')
    assert edited['exit_code'] == 1
    with storage() as db:
        row = db.get(database.ScheduledTask, result['task_id'])
        assert row.name == 'Digest' and row.cron_expression == '15 9 * * 1,3,5'
        assert row.next_run == datetime(2026, 10, 5, 9, 15)
