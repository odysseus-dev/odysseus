"""Execute the round-aware live model-provenance state helper under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_MODULE = (_REPO / "static" / "js" / "chatModelProvenance.js").as_uri()


def test_round_two_fallback_then_provider_alias_does_not_relabel_round_one():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    script = f"""
      import {{ applyModelRouteEventState }} from {json.dumps(_MODULE)};
      const round1 = {{ _requestedModel: 'selected-model', _actualModel: 'selected-model' }};
      const round2 = {{ _requestedModel: 'selected-model', _actualModel: 'selected-model' }};

      const fallbackTarget = applyModelRouteEventState({{
        type: 'fallback', round: 2,
        selected_model: 'selected-model', answered_by: 'backup-model'
      }}, round1, round2, 'selected-model');
      const aliasTarget = applyModelRouteEventState({{
        type: 'model_actual', round: 2,
        requested_model: 'selected-model', model: 'provider-backup-alias'
      }}, round1, round2, 'selected-model');

      console.log(JSON.stringify({{
        fallbackIsRound2: fallbackTarget === round2,
        aliasIsRound2: aliasTarget === round2,
        round1,
        round2,
      }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state == {
        "fallbackIsRound2": True,
        "aliasIsRound2": True,
        "round1": {
            "_requestedModel": "selected-model",
            "_actualModel": "selected-model",
        },
        "round2": {
            "_requestedModel": "selected-model",
            "_actualModel": "provider-backup-alias",
        },
    }


def test_next_round_and_final_metrics_preserve_each_agent_round_route():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    script = f"""
      import {{
        applyModelMetricsState,
        applyModelRouteEventState,
        inheritModelRouteState,
      }} from {json.dumps(_MODULE)};
      const round1 = {{ _requestedModel: 'selected-model', _actualModel: 'selected-model' }};
      const round2 = {{}};
      inheritModelRouteState(round1, round1, round2, 'selected-model');
      applyModelRouteEventState({{
        type: 'fallback', round: 2,
        selected_model: 'selected-model', answered_by: 'backup-model'
      }}, round1, round2, 'selected-model');
      applyModelRouteEventState({{
        type: 'model_actual', round: 2,
        requested_model: 'selected-model', model: 'provider-backup-alias'
      }}, round1, round2, 'selected-model');

      const round3 = {{}};
      inheritModelRouteState(round1, round2, round3, 'selected-model');
      const metricsTarget = applyModelMetricsState({{
        requested_model: 'selected-model',
        model: 'provider-backup-alias',
        round_models: ['selected-model', 'provider-backup-alias', 'backup-model'],
      }}, round1, round3, 'selected-model');

      console.log(JSON.stringify({{
        metricsIsRound3: metricsTarget === round3,
        round1,
        round2,
        round3,
      }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "metricsIsRound3": True,
        "round1": {
            "_requestedModel": "selected-model",
            "_actualModel": "selected-model",
        },
        "round2": {
            "_requestedModel": "selected-model",
            "_actualModel": "provider-backup-alias",
        },
        "round3": {
            "_requestedModel": "selected-model",
            "_actualModel": "backup-model",
        },
    }


def test_same_model_fallback_preserves_distinct_endpoint_route_state():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    script = f"""
      import {{ applyModelMetricsState, applyModelRouteEventState }} from {json.dumps(_MODULE)};
      const holder = {{ _requestedModel: 'same-model', _actualModel: 'same-model' }};
      applyModelRouteEventState({{
        type: 'fallback',
        selected_model: 'same-model', answered_by: 'same-model',
        selected_endpoint_id: 'account-one', selected_endpoint_label: 'Account one',
        answered_by_endpoint_id: 'account-two', answered_by_endpoint_label: 'Account two',
      }}, holder, null, 'same-model');
      applyModelMetricsState({{
        requested_model: 'same-model', model: 'same-model',
        requested_endpoint_id: 'account-one', requested_endpoint_label: 'Account one',
        endpoint_id: 'account-two', endpoint_label: 'Account two',
      }}, holder, null, 'same-model');
      console.log(JSON.stringify(holder));
    """
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "_requestedModel": "same-model",
        "_actualModel": "same-model",
        "_requestedEndpointId": "account-one",
        "_requestedEndpointLabel": "Account one",
        "_actualEndpointId": "account-two",
        "_actualEndpointLabel": "Account two",
    }


def test_metrics_preserve_explicitly_unknown_round_endpoint():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    script = f"""
      import {{ applyModelMetricsState }} from {json.dumps(_MODULE)};
      const holder = {{
        _requestedModel: 'same-model',
        _actualModel: 'same-model',
        _requestedEndpointId: 'account-one',
        _requestedEndpointLabel: 'Account one',
      }};
      const roundHolder = {{}};
      applyModelMetricsState({{
        requested_model: 'same-model', model: 'same-model',
        requested_endpoint_id: 'account-one', requested_endpoint_label: 'Account one',
        endpoint_id: 'account-two', endpoint_label: 'Account two',
        round_endpoint_ids: [null], round_endpoint_labels: [null],
      }}, holder, roundHolder, 'same-model');
      console.log(JSON.stringify(roundHolder));
    """
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "_requestedModel": "same-model",
        "_actualModel": "same-model",
        "_requestedEndpointId": "account-one",
        "_requestedEndpointLabel": "Account one",
        "_actualEndpointId": None,
        "_actualEndpointLabel": None,
    }
