import logging
from pathlib import Path

from src.model_capability_readers import records_from_payload


def test_normalization_debug_log_reports_shape_without_payload_identity(caplog):
    payload = {
        "data": [
            {
                "id": "sensitive-model-id",
                "architecture": {"modality": "text+image->text"},
                "supported_parameters": ["tools", "temperature"],
                "private_field": "secret-value",
            }
        ]
    }

    with caplog.at_level(logging.DEBUG, logger="src.model_capability_readers"):
        records = records_from_payload(payload, vendor="openrouter")

    assert len(records) == 1
    message = caplog.messages[-1]
    assert "[model-capability] normalized:" in message
    assert "canonical_version=1" in message
    assert "provider=openrouter" in message
    assert "provider_source=explicit" in message
    assert "catalog_shape=openrouter.models.rich.v1" in message
    assert "fallback=False" in message
    assert "records=1" in message
    assert "families=['chat']" in message
    assert "features=['tool_call', 'vision']" in message
    assert "controls=['temperature']" in message
    assert "sensitive-model-id" not in message
    assert "secret-value" not in message


def test_fallback_debug_log_is_explicit_and_has_no_capability_claims(caplog):
    payload = [{"id": "future-model", "capabilities": {"tools": True}}]

    with caplog.at_level(logging.DEBUG, logger="src.model_capability_readers"):
        records = records_from_payload(payload, vendor="future-provider")

    assert records[0].capability.capabilities == ()
    message = caplog.messages[-1]
    assert "provider=future_provider" in message
    assert "catalog_shape=fallback.models.list.v1" in message
    assert "fallback=True" in message
    assert "features=[]" in message


def test_web_app_logging_uses_existing_log_level_environment_toggle():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")

    assert 'os.getenv("LOG_LEVEL", "INFO")' in source
    assert "_root_logger.setLevel(_log_level)" in source
    assert "_console_h.setLevel(_log_level)" in source
    assert "_file_h.setLevel(_log_level)" in source
    assert "log_level=_log_level_name.lower()" in source
