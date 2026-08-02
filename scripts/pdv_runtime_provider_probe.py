#!/usr/bin/env python3
"""Bounded provider proof: authorization guard plus one synthetic LLM request."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.llm_core import llm_call
from src.pdv_provider_guard import get_last_authorization_receipt, record_provider_outcome_sync


def main() -> int:
    endpoint = os.environ.get("PDV_PROOF_ENDPOINT", "").strip()
    model = os.environ.get("PDV_PROOF_MODEL", "").strip()
    if endpoint != "http://127.0.0.1:11435/v1" or not model:
        print(json.dumps({"ok": False, "reason": "invalid proof route"}))
        return 2
    started = time.monotonic()
    try:
        response = llm_call(
            endpoint,
            model,
            [{"role": "user", "content": "Reply with exactly: PDV_ODYSSEUS_PROVIDER_OK"}],
            temperature=0,
            max_tokens=16,
            timeout=60,
        )
    except Exception as error:
        authorization = get_last_authorization_receipt() or {}
        reason_code = "HERMES_REQUEST_TIMEOUT" if "timed out" in str(error).lower() else "HERMES_REQUEST_FAILED"
        outcome_receipt = None
        if authorization:
            try:
                outcome_receipt = record_provider_outcome_sync(
                    authorization,
                    "timed_out" if reason_code == "HERMES_REQUEST_TIMEOUT" else "failed",
                    round((time.monotonic() - started) * 1000),
                    cost_microusd=0,
                )
            except Exception:
                outcome_receipt = None
        print(json.dumps({
            "ok": False,
            "reason_code": reason_code,
            "providerRequestId": authorization.get("provider_request_id"),
            "authorizationReceiptId": authorization.get("authorization_receipt_id"),
            "authorizationReasonCode": authorization.get("reason_code"),
            "selectedEndpoint": authorization.get("selected_endpoint"),
            "outcomeReceiptId": (outcome_receipt or {}).get("outcome_receipt_id"),
            "outcomeRecorded": outcome_receipt is not None,
            "responseContentPersisted": False,
            "maxTokens": 16,
        }))
        return 1
    authorization = get_last_authorization_receipt() or {}
    outcome_receipt = record_provider_outcome_sync(
        authorization,
        "completed",
        round((time.monotonic() - started) * 1000),
        cost_microusd=0,
    )
    encoded = response.encode("utf-8")
    print(json.dumps({
        "ok": bool(response.strip()),
        "endpoint": "http://127.0.0.1:11435/v1",
        "model": model,
        "responseChars": len(response),
        "responseSha256": hashlib.sha256(encoded).hexdigest(),
        "responseContentPersisted": False,
        "maxTokens": 16,
        "providerRequestId": authorization.get("provider_request_id"),
        "authorizationReceiptId": authorization.get("authorization_receipt_id"),
        "authorizationReasonCode": authorization.get("reason_code"),
        "selectedEndpoint": authorization.get("selected_endpoint"),
        "outcomeReceiptId": outcome_receipt.get("outcome_receipt_id"),
        "outcomeRecorded": True,
    }))
    return 0 if response.strip() else 1


if __name__ == "__main__":
    raise SystemExit(main())
