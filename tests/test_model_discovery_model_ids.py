"""A malformed provider row must not hide its valid neighboring models."""
import pytest

from src.model_discovery import ModelDiscovery


@pytest.mark.parametrize('wrapped', [False, True], ids=['bare', 'openai'])
@pytest.mark.parametrize('invalid_id', [None, '', 0, False, [], {}, 42, True, ['bad'], {'bad': 'id'}])
def test_discovery_keeps_valid_models_beside_invalid_ids(monkeypatch, wrapped, invalid_id):
    discovery = ModelDiscovery('fixture')
    rows = [{'id': '/valid-model'}, {'id': invalid_id}, 'not a row', None, {'id': 'second-model'}]
    payload = {'data': rows} if wrapped else rows

    class Response:
        is_success = True
        def json(self):return payload

    monkeypatch.setattr('src.model_discovery.httpx.get', lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(discovery, '_fingerprint_provider', lambda *_:'fixture')
    result = discovery._check_port('fixture', 8080)
    assert result is not None
    assert result['models'] == ['/valid-model', 'second-model']
    assert result['models_display'] == ['valid-model', 'second-model']
    assert result['provider'] == 'fixture'


@pytest.mark.parametrize('payload', [{'data': [{'id': 42}]}, [{'id': ['bad']}], {'data': []}])
def test_discovery_with_no_valid_ids_stays_unavailable(monkeypatch, payload):
    class Response:
        is_success = True
        def json(self):return payload
    monkeypatch.setattr('src.model_discovery.httpx.get', lambda *_args, **_kwargs:Response())
    assert ModelDiscovery('fixture')._check_port('fixture',8080) is None
