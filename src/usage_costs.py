"""Costs by period, provider/model, profile and project, read from Hermes' OWN stored accounting (read-only SQLite).

Nothing is re-priced and no analytics database is added: every figure was priced by Hermes when the call happened
(``session_model_usage``: one row per session, model, billing route and task, so auxiliary calls such as titles and approvals are
included). This module only labels each stored figure so four different kinds of number are never added together:

  billed                   provider-confirmed charge          (cost_status "actual")
  estimated_api            tokens x API list prices           (cost_status "estimated" on a pay-per-token route)
  subscription_equivalent  what the same usage would cost on the API; NOT a charge (an estimate from a subscription route)
  included                 free / subscription-included / local; no API charge is invented
  unpriced                 no price data; shown as "Unpriced", never as $0.00

``cost_class`` must stay identical to ``gateway/session_usage.py::cost_class`` in the reviewed Hermes patch; a shared fixture
(tests/projects_dashboard/cost_class_cases.json) is run against both.

Periods count a session in the period of its LAST activity (Hermes stores cumulative totals per session, not per call), so a
long-running conversation is attributed whole to the period it was last used in. The response says so.
"""
from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path

CLASSES = ('billed', 'estimated_api', 'subscription_equivalent', 'included', 'unpriced')
PRICED = ('billed', 'estimated_api', 'subscription_equivalent')
SOURCE_LABELS = {
    'provider_cost_api': 'Provider-reported cost (from the provider or its CLI)',
    'provider_generation_api': 'Provider generation API',
    'provider_models_api': 'Provider models API price table',
    'official_docs_snapshot': 'Official price snapshot built into Hermes',
    'user_override': 'Your price override',
    'custom_contract': 'Custom contract price',
    'none': 'No price source',
}
SCOPE_NOTE = ('Counts only what this Hermes installation recorded, not other clients on the same account. '
              'A session is counted in the period of its last activity because Hermes stores per-session totals.')
MAX_ROWS = 60


def cost_class(status, provider='', billing_mode=''):
    status = str(status or 'unknown').lower()
    if status == 'actual':
        return 'billed'
    if status == 'included':
        return 'included'
    if status == 'estimated':
        subscription = 'subscription' in str(provider or '').lower() or str(billing_mode or '').lower().startswith('subscription')
        return 'subscription_equivalent' if subscription else 'estimated_api'
    return 'unpriced'


def _ro(path):
    return sqlite3.connect('file:' + str(path).replace('\\', '/') + '?mode=ro', uri=True, timeout=2)


def _norm(path):
    return str(path or '').replace('\\', '/').rstrip('/').lower()


def _project_folders(projects_db):
    """(normalised folder, project name), longest folders first so the most specific project wins. Empty when unreadable."""
    if not projects_db or not Path(projects_db).is_file():
        return []
    try:
        with _ro(projects_db) as db:
            rows = db.execute('SELECT f.path, p.name FROM project_folders f JOIN projects p ON p.id = f.project_id WHERE p.archived = 0').fetchall()
    except sqlite3.Error:
        return []
    return sorted(((_norm(path), str(name)) for path, name in rows if path), key=lambda item: -len(item[0]))


def _project_of(cwd, folders):
    here = _norm(cwd)
    if not here:
        return None
    for folder, name in folders:
        if here == folder or here.startswith(folder + '/'):
            return name
    return None


def period_starts(now):
    local = time.localtime(now)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    return {'today': midnight, 'last_7_days': now - 7 * 86400, 'last_30_days': now - 30 * 86400}


def _blank():
    return {'usd': Decimal(0), 'calls': 0, 'aux_calls': 0, 'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'reasoning': 0, 'sources': set(), 'sessions': set()}


def _add(bucket, row, kind):
    bucket['calls'] += int(row['api_call_count'] or 0)
    bucket['aux_calls'] += int(row['api_call_count'] or 0) if row['task'] else 0
    for key, column in (('input', 'input_tokens'), ('output', 'output_tokens'), ('cache_read', 'cache_read_tokens'),
                        ('cache_write', 'cache_write_tokens'), ('reasoning', 'reasoning_tokens')):
        bucket[key] += int(row[column] or 0)
    if kind in PRICED:
        amount = row['actual_cost_usd'] if kind == 'billed' and row['actual_cost_usd'] is not None else row['estimated_cost_usd']
        bucket['usd'] += Decimal(str(amount or 0))
    if row['cost_source']:
        bucket['sources'].add(row['cost_source'])
    bucket['sessions'].add(row['session_id'])


def _finish(kind, bucket):
    priced = kind in PRICED
    return {'kind': kind, 'usd': float(bucket['usd']) if priced else None, 'calls': bucket['calls'], 'aux_calls': bucket['aux_calls'],
            'input': bucket['input'], 'output': bucket['output'], 'cache_read': bucket['cache_read'], 'cache_write': bucket['cache_write'],
            'reasoning': bucket['reasoning'], 'sessions': len(bucket['sessions']),
            'price_sources': [{'id': s, 'label': SOURCE_LABELS.get(s, s)} for s in sorted(bucket['sources'])]}


def overview(state_db, projects_db=None, *, now=None):
    """``{periods: {name: {by_class, by_model, by_profile, by_project}}, scope, generated_at}``; ``available`` False when unreadable."""
    now = now or time.time()
    result = {'available': False, 'generated_at': now, 'scope': SCOPE_NOTE, 'classes': list(CLASSES), 'periods': {}}
    if not state_db or not Path(state_db).is_file():
        result['reason'] = 'The Hermes session store was not found.'
        return result
    try:
        with _ro(state_db) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                'SELECT u.session_id, u.model, u.billing_provider, u.billing_mode, u.task, u.api_call_count, u.input_tokens, u.output_tokens, '
                'u.cache_read_tokens, u.cache_write_tokens, u.reasoning_tokens, u.estimated_cost_usd, u.actual_cost_usd, u.cost_status, '
                'u.cost_source, u.first_seen, u.last_seen, s.cwd, s.profile_name FROM session_model_usage u LEFT JOIN sessions s ON s.id = u.session_id').fetchall()
    except sqlite3.Error as exc:
        result['reason'] = 'The Hermes session store could not be read (%s).' % type(exc).__name__
        return result
    folders = _project_folders(projects_db)
    starts = period_starts(now)
    starts['all_recorded'] = 0.0
    for name, start in starts.items():
        by_class, by_model, by_profile, by_project = {}, {}, {}, {}
        counted, straddling = set(), set()
        for row in rows:
            if float(row['last_seen'] or 0) < start:
                continue
            counted.add(row['session_id'])
            if start and float(row['first_seen'] or row['last_seen'] or 0) < start:
                straddling.add(row['session_id'])
            kind = cost_class(row['cost_status'], row['billing_provider'], row['billing_mode'])
            _add(by_class.setdefault(kind, _blank()), row, kind)
            _add(by_model.setdefault((row['billing_provider'] or '', row['model'] or '', kind), _blank()), row, kind)
            _add(by_profile.setdefault((row['profile_name'] or 'default', kind), _blank()), row, kind)
            project = _project_of(row['cwd'], folders)
            if project:
                _add(by_project.setdefault((project, kind), _blank()), row, kind)
        models = [dict(_finish(k[2], b), provider=k[0], model=k[1]) for k, b in by_model.items()]
        models.sort(key=lambda m: (-(m['usd'] or 0), -(m['input'] + m['output'])))
        result['periods'][name] = {
            'sessions': len(counted), 'sessions_begun_earlier': len(straddling),
            'by_class': [_finish(kind, by_class[kind]) for kind in CLASSES if kind in by_class],
            'by_model': models[:MAX_ROWS],
            'by_profile': [dict(_finish(k[1], b), profile=k[0]) for k, b in sorted(by_profile.items())][:MAX_ROWS],
            'by_project': [dict(_finish(k[1], b), project=k[0]) for k, b in sorted(by_project.items())][:MAX_ROWS],
        }
    result['available'] = True
    result['project_attribution'] = 'a conversation is attributed to a registered project when its working folder is inside one of that project\'s folders'
    return result
