"""One presentation gate between agent execution and externally visible prose.

Tool, progress and interaction events stay live. Answer deltas are held until
the generator unwinds so a later replacement cannot conceal an earlier false
claim. This consumes no provider calls. Cancellation closes the inner generator
under the same journal/turn authority; it never emits a successful terminal event.
"""
from __future__ import annotations

from contextlib import aclosing
from dataclasses import replace
from functools import wraps
from inspect import signature
import json
import re
from time import perf_counter

from src.agent_evidence import (
    CompletionDecision, CompletionStatus, EvidenceKind, EvidenceLedger,
    requirements_from_runtime_context, _execution_obligation, _unquoted_statements,
    _ARTIFACT_PATH,
)
from .effect_log import EffectLog
from .journal import ActionJournal, bind_journal, current_journal


def _ledger(journal: ActionJournal, requirements) -> EvidenceLedger:
    """The single evidence view used for the decision and the prose filter."""
    ledger = EvidenceLedger.from_tool_events(journal.evidence_events(), requirements)
    ledger.record_effects(journal.effect_entries(),
                          {action.action_id: index for index, action in enumerate(journal.actions, 1)},
                          journal.partial_reads())
    return ledger


_TEST_CLAIM = re.compile(
    r'\b(?:(?:all\s+)?(?:tests?|checks?|verification|suite)\s+(?:have\s+|has\s+|now\s+|are\s+|is\s+)*(?:passed|passing|successful|green)|'
    r'(?:passed|passing)\s+(?:all\s+)?(?:the\s+)?tests?|\d+\s+passed)\b', re.I)
_TEST_STATUS_CLAIM = re.compile(
    r'\b(?:tests?|pytest|unittest|test suite|checks?|verification)\s*[:—-]?\s*'
    r'(?:all\s+|have\s+|has\s+|now\s+|are\s+|is\s+|ran\s+)*'
    r'(?:pass(?:ed|ing)?|succeeded|successful(?:ly)?|green)\b|'
    r'\b(?:zero|no|0)\s+(?:test\s+)?failures\b', re.I)
_EXECUTION_CLAIM = re.compile(
    r'\b(?:(?:I|we|I\'ve|we\'ve|and)\s+(?:have\s+)?(?:successfully\s+)?(?:ran|executed|tested|verified|created|updated|modified|wrote|saved|fixed|completed|sent|deleted|submitted|published|deployed|configured|uploaded)|'
    rf'(?:file|artifact|command|script|service|server|email|message|record|resource|{_ARTIFACT_PATH})\s+(?:was\s+|has\s+been\s+|is\s+)?(?:successfully\s+)?(?:created|updated|written|saved|executed|started|sent|deleted|submitted|published|deployed|configured)|'
    r'(?:successfully\s+)(?:ran|executed|created|updated|saved|completed|sent|deleted|submitted|published|deployed)|'
    r'(?:the\s+)?(?:remote\s+)?(?:operation|request|call|mutation|action)\s+(?:was\s+|has\s+)?(?:successfully\s+)?(?:completed|succeeded|finished))\b', re.I)
_UNATTESTED_TEST_METRIC = re.compile(
    r'\b\d+\s+(?:(?:unit|integration)\s+)?tests?\s+pass(?:ed|ing)?\b|'
    r'\b\d+\s+passed\b|\b\d+(?:\.\d+)?%\s+(?:test\s+)?coverage\b', re.I)
_UNBOUNDED_SUCCESS = re.compile(
    r'\b(?:everything|all\s+(?:bugs|issues))\s+(?:is\s+|are\s+|has\s+been\s+)?'
    r'(?:fixed|resolved|working)\b', re.I)
_MUTATION_CLAIM = re.compile(
    r'\b(?:created|updated|modified|wrote|written|saved|fixed|sent|deleted|submitted|published|deployed|configured|uploaded)\b', re.I)
_TEST_IDENTITY = re.compile(r'\b(?:pytest|unittest)\b', re.I)
_TEST_SUBJECT = re.compile(r'\b(?:tests?|test suite|pytest|unittest|checks?|verification)\b', re.I)
_CLAIM_PATH = re.compile(_ARTIFACT_PATH)
_BARE_SUCCESS = re.compile(r'^\s*(?:done|completed|success|all done|all set|fixed)[.!]?\s*$', re.I)
_NON_REPORT_SCOPE = re.compile(
    r'^\s*(?:if|unless|suppose|imagine|hypothetically|for\s+(?:example|instance))\b|'
    r'\b(?:if|when|whenever|unless|until)\b|'
    r'\b(?:can|could|may|might|should|would|will|must)\b|'
    r'\b(?:says?|said|states?|stated|example)\b', re.I)


def _current_run_claims(statement: str, *, execution_required: bool) -> list[tuple[str, str]]:
    """Classify asserted execution, separately from the turn's obligation.

    Past actions and current result/status predicates are reports. Conditional,
    modal, attributed and example clauses are scoped prose. Bare terminal
    success only carries execution meaning under an execution contract.
    """
    if _BARE_SUCCESS.fullmatch(statement):
        return [('terminal', statement)] if execution_required else []
    actions = list(_EXECUTION_CLAIM.finditer(statement))
    leading = re.match(r'^\s*(?:successfully\s+)?(?:created|updated|modified|wrote|saved)\b', statement, re.I)
    if leading:
        actions.insert(0, leading)
    candidates = [('action', match) for match in actions]
    for kind, pattern in [('metric', _UNATTESTED_TEST_METRIC), ('metric', _UNBOUNDED_SUCCESS),
                          ('test', _TEST_CLAIM), ('test', _TEST_STATUS_CLAIM)]:
        candidates.extend((kind, match) for match in pattern.finditer(statement))
    claims = []
    for kind, match in candidates:
        # Scope markers after an asserted action do not make that action
        # hypothetical ("I ran pytest to see if ..."). An immediate conditional
        # continuation does qualify a result ("Tests passed if ...").
        if _NON_REPORT_SCOPE.search(statement[:match.start()]) or re.match(
                r'\s+(?:if|when|whenever|unless|until)\b', statement[match.end():], re.I):
            continue
        end = next((action.start() for action in actions if action.start() > match.start()), len(statement))
        scope = statement[match.start():end]
        if kind == 'action':
            if _MUTATION_CLAIM.search(match.group()):
                kind = 'mutation'
            elif _TEST_SUBJECT.search(scope):
                kind = 'test'
            else:
                kind = 'execution'
        claims.append((kind, scope))
    return claims


def _supported_prose(text: str, ledger: EvidenceLedger, decision: CompletionDecision) -> tuple[str, str]:
    """Remove unsupported assertions at statement boundaries; add no notice."""
    incomplete = decision.reason if not decision.can_complete and decision.status != CompletionStatus.AWAITING_USER else ''
    execution_required = _execution_obligation(ledger.requirements)
    # Bare "Done." cannot stand for an external effect nobody verified.
    terminal_claims = execution_required or bool(ledger.unverified_external_effects())
    kept = []
    removed = ''
    for statement, scoped in _unquoted_statements(text):
        why = ''
        for claim, scope in _current_run_claims(scoped, execution_required=terminal_claims):
            paths = tuple(match.group().rstrip('.') for match in _CLAIM_PATH.finditer(scope))
            if claim == 'metric':
                why = 'test counts, coverage or exhaustive correctness were not established by execution evidence'
            elif claim == 'test':
                identities = tuple(match.group().lower() for match in _TEST_IDENTITY.finditer(scope))
                if (decision.status not in {CompletionStatus.VERIFIED, CompletionStatus.UNVERIFIED}
                        or not ledger._supports_verifier_claim(identities, paths)):
                    why = 'no current passing executable verification supports the claim'
            elif claim == 'mutation':
                if not ledger._supports_artifact_claim(EvidenceKind.ARTIFACT_MUTATION, paths):
                    why = 'no matching artifact mutation supports the execution claim'
            elif claim == 'execution':
                # A generic assertion cannot be tied confidently to a receipt.
                why = 'no matching operation supports the execution claim'
            elif claim == 'terminal' and decision.status not in {CompletionStatus.SATISFIED, CompletionStatus.VERIFIED}:
                why = incomplete or 'no successful execution supports completion'
            if why:
                break
        if why:
            removed = removed or why
        else:
            kept.append(statement)
    prose = ''.join(kept).strip() if removed else text
    return prose, removed


def completion_answer(text: str, ledger: EvidenceLedger, decision: CompletionDecision) -> tuple[str, str]:
    """Keep explanatory prose; remove unsupported assertions and attach facts.

    Exit status proves neither test counts nor coverage. A bad assertion is
    removed at statement boundaries instead of erasing an entire explanation.
    The execution outcome remains separate from a discarded model assertion.
    Unverified external effects are always stated by the server, so no
    surviving prose can present a reported remote success as a verified one.
    """
    answer, reason = _completion_answer(text, ledger, decision)
    return _disclose(answer, ledger), reason


def _disclose(answer: str, ledger: EvidenceLedger) -> str:
    """Append the server's facts for unverified external effects."""
    disclosure = _disclosure(answer, ledger)
    return answer.rstrip() + disclosure if disclosure else answer


def _disclosure(answer: str, ledger: EvidenceLedger) -> str:
    """Build the complete server-owned disclosure independently of prose length."""
    summary = ' '.join(ledger.effect_disclosures())
    if not summary:
        return ''
    return ('\n\n' + summary) if answer.strip() else summary


def _completion_answer(text: str, ledger: EvidenceLedger, decision: CompletionDecision) -> tuple[str, str]:
    incomplete = decision.reason if not decision.can_complete and decision.status != CompletionStatus.AWAITING_USER else ''
    execution_required = _execution_obligation(ledger.requirements)
    prose, removed = _supported_prose(text, ledger, decision)
    if incomplete or (removed and execution_required and decision.status in {CompletionStatus.UNVERIFIED, CompletionStatus.AWAITING_USER}):
        reason = incomplete or removed
        missing = (' Missing artifacts: ' + ', '.join(decision.missing_artifacts) + '.'
                   if decision.missing_artifacts else '')
        notice = 'The task is incomplete: ' + reason.rstrip('.') + '.' + missing
        recorded = [path for path in ledger.requirements.required_artifacts
                    if ledger._supports_artifact_claim(EvidenceKind.ARTIFACT_MUTATION, (path,))]
        if removed and recorded:
            notice += ' Recorded artifact mutation: ' + ', '.join(recorded) + '.'
        return notice + ('\n\n' + prose if prose.strip() else ''), reason
    if removed and not execution_required and decision.status != CompletionStatus.VERIFIED:
        notice = 'Unsupported execution claims were omitted: ' + removed.rstrip('.') + '.'
        return (prose.rstrip() + '\n\n' + notice) if prose.strip() else notice, removed
    if decision.can_complete and (ledger.requirements.required_artifacts or ledger.requirements.verifier_required or removed):
        facts = []
        if ledger.requirements.required_artifacts:
            facts.append('Output available: ' + ', '.join(ledger.requirements.required_artifacts) + '.')
        if decision.status == CompletionStatus.VERIFIED or ledger._supports_verifier_claim():
            facts.append('The latest executable verification passed.')
        elif any(e.kind == EvidenceKind.ARTIFACT_VALIDATION and e.authoritative and e.success for e in ledger.events):
            facts.append('Artifact readback verified. No passing executable test result was recorded.')
        else:
            facts.append('No passing executable test result was recorded.')
        summary = ' '.join(facts)
        return (prose.rstrip() + '\n\n' + summary) if prose.strip() else summary, removed
    return prose, removed


def _event(data: dict) -> str:
    return 'data: ' + json.dumps(data) + '\n\n'


def with_completion_gate(func):
    call_signature = signature(func)

    @wraps(func)
    async def wrapped(*args, **kwargs):
        started = perf_counter()
        first_answer_at = None
        arguments = call_signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        bound = arguments.arguments
        messages = bound.get('messages') or []
        instruction = next((m.get('content', '') for m in reversed(messages)
                            if m.get('role') == 'user' and isinstance(m.get('content'), str)), '')
        context = bound.get('client_runtime_context') or {}
        requirements = requirements_from_runtime_context(context, instruction=instruction)
        from src.tool_execution import vet_workspace
        # A completion declaration is not a filesystem permission. Only the
        # explicit, vetted runtime workspace may be read for artifact versions.
        trusted_workspace = vet_workspace(bound.get('workspace')) if bound.get('workspace') else ''
        requirements = replace(requirements, workspace_root=trusted_workspace or '')
        parent = current_journal()
        journal = ActionJournal(
            workspace=requirements.workspace_root, observed_artifacts=requirements.required_artifacts,
            parent_run_id=bound.get('_parent_run_id') or (parent.run_id if parent is not None else None))
        # One durable effect log per run lineage gives child effects and parent
        # observations a single total order for invalidation.
        journal.effects = (parent.effects if parent is not None and parent.effects is not None
                           else EffectLog(journal.run_id))
        answer_events: list[dict] = []
        metrics_events: list[dict] = []
        answer = ''
        has_final = False
        done = False
        awaiting = False
        exhausted = False
        provider_error: str | None = None
        with bind_journal(journal):
            async with aclosing(func(*args, **kwargs)) as stream:
                async for chunk in stream:
                    if chunk.strip() == 'data: [DONE]':
                        done = True
                        continue
                    try:
                        data = json.loads(chunk[6:]) if chunk.startswith('data: ') else None
                    except (ValueError, TypeError):
                        data = None
                    if not isinstance(data, dict):
                        if chunk.startswith('event: error'):
                            # The inner stream may still emit failed-terminal
                            # diagnostics. Hold the original error until those
                            # and the buffered answer have been released.
                            provider_error = provider_error or chunk
                            continue
                        yield chunk
                        continue
                    kind = data.get('type')
                    if kind == 'completion_decision':
                        existing = data.get('data') or {}
                        awaiting |= existing.get('status') == 'awaiting_user'
                        exhausted |= existing.get('status') == 'exhausted'
                        continue
                    if kind in {'metrics', 'agent_terminal'}:
                        metrics_events.append(data)
                        declared = (data.get('data') or {}).get('completion_requirements')
                        awaiting |= bool((data.get('data') or {}).get('missing_workspace'))
                        if isinstance(declared, dict):
                            requirements = requirements_from_runtime_context({'completion_requirements': declared})
                            requirements = replace(requirements, workspace_root=trusted_workspace or '')
                            # New obligations affect future receipts only. Never
                            # backfill historical versions with present bytes.
                            journal.observed_artifacts = tuple(dict.fromkeys(
                                (*journal.observed_artifacts, *requirements.required_artifacts)))
                        continue
                    if kind == 'ask_user':
                        awaiting = True
                        payload = data.get('data') or {}
                        if isinstance(payload.get('question'), str):
                            current = _ledger(journal, requirements)
                            question, why = completion_answer(payload['question'], current, current.evaluate(awaiting_user=True))
                            if why:
                                data = {**data, 'data': {**payload, 'question': question}}
                                chunk = _event(data)
                    if kind == 'final_response':
                        if first_answer_at is None:
                            first_answer_at = perf_counter()
                        answer = str(data.get('content') or '')
                        has_final = True
                        answer_events.append(data)
                        continue
                    if 'delta' in data or isinstance(data.get('thinking'), str):
                        if first_answer_at is None:
                            first_answer_at = perf_counter()
                        # Boolean thinking=True marks a reasoning-only delta;
                        # a textual thinking companion must not hide an answer
                        # delta. Both shapes remain buffered until the gate.
                        if isinstance(data.get('thinking'), str):
                            answer_events.append({'delta': data['thinking'], 'thinking': True})
                            data = {key: value for key, value in data.items() if key != 'thinking'}
                            if 'delta' not in data:
                                continue
                        if data.get('thinking') is not True and 'delta' in data:
                            if has_final:
                                answer = ''
                                has_final = False
                            answer += str(data.get('delta') or '')
                        answer_events.append(data)
                        continue
                    yield chunk
            if provider_error and not answer_events and not metrics_events:
                yield provider_error
                return
            presentation_replaced = False
            if not provider_error and not has_final and requirements.required_artifacts:
                terminal_texts = next((event.get('data', {}).get('round_texts')
                                       for event in reversed(metrics_events)
                                       if isinstance(event.get('data', {}).get('round_texts'), list)
                                       and all(isinstance(text, str) for text in event['data']['round_texts'])), None)
                if terminal_texts is not None:
                    terminal_answer = '\n\n'.join(text for text in terminal_texts if text.strip())
                    if terminal_answer != answer:
                        # The loop can retract a rejected round while retaining
                        # its live deltas. Do not resurrect those buffered drafts
                        # after recovery. Terminal prose still passes this gate.
                        presentation_replaced = True
                        answer = terminal_answer
                        answer_events = [event for event in answer_events if event.get('thinking') is True]
            ledger = _ledger(journal, requirements)
            decision = ledger.evaluate(exhausted=exhausted, awaiting_user=awaiting)
            if provider_error:
                decision = replace(decision, status=CompletionStatus.FAILED,
                                   can_complete=False, reason='Model request failed')
            # Exhaustion limits execution; factual source synthesis can remain
            # useful and must not be replaced merely because the budget ended.
            presentation_decision = ledger.evaluate(awaiting_user=awaiting) if exhausted and not provider_error else decision
            filtered_answer, reason = _completion_answer(answer, ledger, presentation_decision)
            safe_answer = _disclose(filtered_answer, ledger)
            # Evaluate each earlier draft as well as the final replacement.
            # Never replay an unsupported intermediate success claim.
            draft = ''.join(str(e.get('delta') or e.get('content') or '')
                            + (e['thinking'] if isinstance(e.get('thinking'), str) else '')
                            for e in answer_events)
            _, unsafe_draft = completion_answer(draft, ledger, presentation_decision)
            if not answer.strip() and unsafe_draft:
                reason = reason or unsafe_draft
                safe_answer, _ = completion_answer(draft, ledger, presentation_decision)
            if reason and _execution_obligation(requirements) and decision.can_complete and decision.status == CompletionStatus.UNVERIFIED:
                decision = CompletionDecision(CompletionStatus.UNVERIFIED, False, reason,
                                              decision.evidence_ids, decision.missing_artifacts)
            released_at = perf_counter()
            if not provider_error:
                yield _event({'type': 'completion_decision', 'data': decision.to_dict()})
            # When the only change is the server's effect disclosure, the
            # model's answer events are released unchanged and the disclosure
            # follows them, so no earlier-round text is dropped.
            disclosure = _disclosure(filtered_answer, ledger)
            disclosure_only = bool(disclosure) and not (presentation_replaced or reason or unsafe_draft
                                                        or filtered_answer != answer)
            replaced_answer = not disclosure_only and bool(
                presentation_replaced or reason or unsafe_draft or safe_answer != answer)
            if replaced_answer:
                reasoning = [event for event in answer_events if event.get('thinking') is True]
                _, unsafe_reasoning = completion_answer(
                    ''.join(str(event.get('delta') or '') for event in reasoning), ledger,
                    replace(presentation_decision, can_complete=True))
                if not unsafe_reasoning:
                    for event in reasoning:
                        yield _event(event)
                yield _event({'type': 'final_response', 'content': safe_answer})
            else:
                for event in answer_events:
                    yield _event(event)
                if disclosure_only:
                    yield _event({'delta': disclosure})
            if provider_error:
                yield _event({'type': 'completion_decision', 'data': decision.to_dict()})
            for event in metrics_events:
                metadata = event.setdefault('data', {})
                metadata.update(completion_decision=decision.to_dict(), evidence_events=ledger.to_list(),
                                action_receipts=journal.to_list(), completion_requirements=requirements.to_dict(),
                                run_id=journal.run_id, parent_run_id=journal.parent_run_id)
                if ledger.effects:
                    metadata['effect_assessments'] = [entry['assessment'].to_dict() for entry in ledger.effects]
                metadata['completion_gate'] = {
                    'buffer_seconds': released_at - first_answer_at if first_answer_at is not None else 0,
                    'first_visible_answer_seconds': released_at - started,
                    'additional_provider_calls': 0,
                    'answer_replaced': replaced_answer,
                }
                if replaced_answer:
                    if not provider_error:
                        metadata['round_texts'] = [safe_answer]
                    metadata['completion_gate_reason'] = reason or unsafe_draft or 'receipt_summary'
                elif disclosure_only and metadata.get('round_texts') and isinstance(metadata['round_texts'], list) \
                        and isinstance(metadata['round_texts'][-1], str):
                    # Reload renders round_texts: keep the disclosure with them.
                    metadata['round_texts'] = [*metadata['round_texts'][:-1], metadata['round_texts'][-1].rstrip() + disclosure]
                if provider_error and isinstance(metadata.get('round_texts'), list):
                    # Failed rounds stay as per-round diagnostics, but they are
                    # rendered again on reload. Apply the same statement filter
                    # as the live answer so a rejected claim cannot reappear.
                    metadata['round_texts'] = [
                        _supported_prose(text, ledger, presentation_decision)[0] if isinstance(text, str) else text
                        for text in metadata['round_texts']]
                if isinstance(metadata.get('thinking'), str):
                    _, unsafe_thinking = completion_answer(metadata['thinking'], ledger,
                        replace(presentation_decision, can_complete=True))
                    if unsafe_thinking:
                        metadata.pop('thinking')
                yield _event(event)
            if provider_error:
                yield provider_error
                return
            if done:
                yield 'data: [DONE]\n\n'

    return wrapped
