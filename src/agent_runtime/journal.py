"""Run-owned action history. Model text cannot insert authoritative receipts."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from functools import wraps
from inspect import signature
import logging
from typing import Any
from uuid import uuid4

from .identity import artifact_identity, artifact_version, digest


@dataclass
class ActionReceipt:
    action_id: str
    call_id: str
    proposed_tool: str
    proposed_arguments: str
    provider_arguments: Any = None
    provider_tool: str = ''
    tool: str = ""
    arguments: str = ""
    transitions: list[dict[str, Any]] = field(default_factory=list)
    execution_id: str | None = None
    operation_started: bool = False
    outcome: dict[str, Any] | None = None
    artifact_versions: dict[str, str] = field(default_factory=dict)
    artifact_changes: list[str] | None = None

    def transition(self, stage: str, **details: Any) -> None:
        self.transitions.append({'sequence': len(self.transitions), 'stage': stage, **details})

    def normalize(self, block: Any, reason: str) -> None:
        tool, arguments = str(block.tool_type), str(block.content)
        if tool != self.tool or arguments != self.arguments or not any(t['stage'] == 'normalized' for t in self.transitions):
            self.transition('normalized', reason=reason, tool=tool, arguments=arguments,
                            previous_sha256=digest((self.tool, self.arguments)))
            self.tool, self.arguments = tool, arguments

    def finish(self, result: dict[str, Any]) -> None:
        if self.outcome is not None:
            return
        code = result.get('exit_code')
        valid_code = isinstance(code, int) and not isinstance(code, bool)
        denied = bool(result.get('blocked') or result.get('approval_required')
                      or str(result.get('failure_kind', '')).endswith('_denied'))
        self.outcome = {
            'exit_code': code if valid_code else None,
            'success': valid_code and code == 0 and not result.get('error') and not denied,
            'authoritative': self.execution_id is not None and valid_code and not denied,
            'blocked': denied,
            'output_sha256': digest(result.get('output') or result.get('error') or result.get('stdout') or ''),
        }
        self.transition('outcome', **self.outcome)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionJournal:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    actions: list[ActionReceipt] = field(default_factory=list)
    workspace: str = ''
    observed_artifacts: tuple[str, ...] = ()
    parent_run_id: str | None = None
    # Durable Wave 4 effect log, shared across one run lineage; None disables.
    effects: Any = field(default=None, repr=False, compare=False)
    _dispatches: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def effect_entries(self) -> list[dict[str, Any]]:
        """Effect assessments ordered against this journal's actions.

        Ordinal is the 1-based position of the action in this journal, so the
        ledger can compare effects with receipt-derived evidence. Effects from
        other journals in the lineage carry no ordinal here.
        """
        if self.effects is None:
            return []
        order = {action.action_id: index for index, action in enumerate(self.actions, 1)}
        changes = {action.action_id: action.artifact_changes for action in self.actions}
        history = self.effects.history()
        entries = []
        for assessment in self.effects.assessments():
            claim = history.claim(assessment.effect_id)
            outcome = history.latest_outcome(assessment.effect_id)
            entries.append({
                'ordinal': order.get(assessment.action_id), 'assessment': assessment,
                'tool': claim.operation.tool, 'unknown_scope': claim.unknown_scope, 'external': claim.external,
                'paths': tuple(ref.location[-1] for ref in claim.impact_scope if ref.kind.value == 'filesystem'),
                'mutation_attempted': bool(outcome and outcome.facts.mutation_attempted),
                'artifact_changes': changes.get(assessment.action_id),
            })
        return entries

    def partial_reads(self) -> tuple[str, ...]:
        """Read actions in this journal whose admitted observation was partial."""
        if self.effects is None:
            return ()
        mine = {action.action_id for action in self.actions}
        return tuple(o.source_action_id for o in self.effects.history().observations
                     if o.source_action_id in mine and o.mechanism.value == 'filesystem_read'
                     and o.coverage.value == 'partial')

    def capture_versions(self, action: ActionReceipt) -> None:
        if self.workspace:
            action.artifact_versions = {
                artifact_identity(path, self.workspace): artifact_version(path, self.workspace)
                for path in self.observed_artifacts
            }

    def propose(self, block: Any, call_id: str = '', native_call: dict | None = None) -> ActionReceipt:
        native = native_call or {}
        function = native.get('function') or native
        if not isinstance(function, dict):
            function = {}
        action = ActionReceipt(
            action_id=f'{self.run_id}:action:{len(self.actions) + 1}', call_id=call_id,
            proposed_tool=str(block.tool_type), proposed_arguments=str(block.content),
            provider_arguments=deepcopy(function.get('arguments')),
            provider_tool=str(function.get('name') or ''),
            tool=str(block.tool_type), arguments=str(block.content),
        )
        action.transition('proposed')
        self.actions.append(action)
        return action

    def to_list(self) -> list[dict[str, Any]]:
        return [action.to_dict() for action in self.actions]

    def evidence_events(self) -> list[dict[str, Any]]:
        return [dict(tool=a.tool, command=a.arguments,
                     exit_code=(a.outcome or {}).get('exit_code'),
                     error=not (a.outcome or {}).get('success'),
                     execution_attempted=bool((a.outcome or {}).get('authoritative')),
                     blocked=(a.outcome or {}).get('blocked', False),
                     action_id=a.action_id, execution_id=a.execution_id,
                     artifact_versions=a.artifact_versions, artifact_changes=a.artifact_changes)
                for a in self.actions if a.outcome is not None]


_JOURNAL: ContextVar[ActionJournal | None] = ContextVar('runtime_action_journal', default=None)
_ACTION: ContextVar[ActionReceipt | None] = ContextVar('runtime_current_action', default=None)


@contextmanager
def bind_journal(journal: ActionJournal):
    token = _JOURNAL.set(journal)
    action_token = _ACTION.set(None)
    try:
        yield journal
    finally:
        _ACTION.reset(action_token)
        _JOURNAL.reset(token)


def current_journal() -> ActionJournal | None:
    return _JOURNAL.get()


def propose_action(block: Any, call_id: str = '', native_call: dict | None = None) -> ActionReceipt | None:
    journal = _JOURNAL.get()
    return journal.propose(block, call_id, native_call) if journal else None


def mark_authorized() -> None:
    action = _ACTION.get()
    if action is not None and not any(t['stage'] == 'authorized' for t in action.transitions):
        action.transition('authorized', authority='existing_dispatcher_policy')


def mark_dispatch() -> None:
    action = _ACTION.get()
    if action is not None and action.execution_id is None:
        journal = _JOURNAL.get()
        if journal is not None and journal.effects is not None:
            # Durable claim first. If it cannot be persisted this raises and
            # the action stays undispatched: the backend is never invoked.
            from .effect_adapters import begin_effect
            capture = begin_effect(journal, action)
            journal._dispatches[action.action_id] = capture
            if capture.claim is not None:
                action.transition('effect_claimed', effect_id=capture.claim.effect_id,
                                  sequence=capture.claim.sequence)
        mark_authorized()
        action.execution_id = action.action_id + ':execution:1'
        action.transition('dispatched', execution_id=action.execution_id)


async def dispatched(operation):
    """Record an actual backend invocation, distinct from router admission."""
    try:
        mark_dispatch()
    except BaseException:
        close = getattr(operation, 'close', None)
        if close is not None:
            close()  # never invoked; do not leave an un-awaited coroutine
        raise
    return await operation


def _settle(journal: ActionJournal | None, action: ActionReceipt, **outcome: Any) -> None:
    if journal is None or journal.effects is None:
        return
    capture = journal._dispatches.pop(action.action_id, None)
    if capture is None:
        return
    from .effect_adapters import settle_effect
    try:
        settle_effect(journal, action, capture, **outcome)
    except Exception:  # noqa: BLE001 - bookkeeping must not alter the tool result
        # The claim stays unsettled (ATTEMPTED), which assesses as pending
        # with possible impact: conservative, never a manufactured success.
        journal.effects.degraded = True
        logging.getLogger(__name__).warning('Effect outcome could not be recorded', exc_info=True)


def mark_operation_started(backend: str, **details: Any) -> None:
    action = _ACTION.get()
    if action is not None:
        action.operation_started = True
        action.transition('operation_started', backend=backend, **details)


async def execute_action(executor, action: ActionReceipt | None, block: Any, **kwargs):
    """Adapter binds the proposal across async tool-task execution and cleanup."""
    if action is not None:
        action.normalize(block, 'agent_loop compatibility adapters')
    token = _ACTION.set(action)
    try:
        return await executor(block, **kwargs)
    finally:
        _ACTION.reset(token)


def record_action(func):
    call_signature = signature(func)

    @wraps(func)
    async def wrapped(*args, **kwargs):
        bound = call_signature.bind(*args, **kwargs)
        block = bound.arguments['block']
        action = _ACTION.get() or propose_action(block)
        token = _ACTION.set(action)
        try:
            journal = current_journal()
            before = {}
            if action is not None:
                action.normalize(block, 'dispatcher input')
                if journal is not None and journal.workspace:
                    journal.capture_versions(action)
                    before = dict(action.artifact_versions)
            description, result = await func(*args, **kwargs)
            if action is not None:
                journal = current_journal()
                if journal is not None:
                    journal.capture_versions(action)
                    if journal.workspace:
                        action.artifact_changes = [key for key, value in action.artifact_versions.items()
                                                   if before.get(key) != value]
                if 'BLOCKED' in description and action.execution_id is None:
                    action.transition('authorization_denied', reason=str(result.get('error', '')))
                    action.finish({**result, 'blocked': True})
                else:
                    action.finish(result)
                # Structured producer facts are projected here, before the
                # receipt reduction drops them.
                _settle(journal, action, result=result)
            return description, result
        except BaseException as exc:
            if action is not None:
                action.transition('interrupted', category=type(exc).__name__)
                _settle(current_journal(), action, error=exc)
            raise
        finally:
            _ACTION.reset(token)

    return wrapped
