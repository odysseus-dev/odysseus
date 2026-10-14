"""Explicit collaborators for execution and audit coordination.

The transport compatibility bindings supply the original patchable functions.
Services never import the skills transport module or copy credentials into a
second state store.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AuditDependencies:
    test_jobs: dict
    audit_jobs: dict
    test_task: Callable
    test_messages: Callable
    baseline_messages: Callable
    evaluate_run: Callable
    evaluate_necessity: Callable
    evaluate_retrieval_precision: Callable
    should_check_retrieval_precision: Callable
    improve_markdown: Callable
    apply_markdown: Callable
    duplicate_blocker: Callable
    finalize_status: Callable
    finalize_batch: Callable
    auto_publish_policy: Callable
    utility_blocker: Callable
    verdict_efficiency: Callable
    run_test_job: Callable
    run_arm: Callable
    run_test_once: Callable
    audit_one: Callable
    run_batch: Callable
    resolve_models: Callable
