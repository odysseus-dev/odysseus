import pytest
import json
from unittest.mock import patch, mock_open, MagicMock
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import services.trace_export

# --- App Setup ---
app = FastAPI()

@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

@app.post("/api/trace/export")
def mock_export_endpoint(payload: dict):
    # FIX: Reference it via the module name so the mock can intercept it
    data = services.trace_export.build_trace_records(
        db=None, 
        current_user=payload.get("user"), 
        message_ids=payload.get("message_ids"), 
        session_id=payload.get("session_id"), 
        label=payload.get("label")
    )
    return {"status": "success", "data": data}

client = TestClient(app, raise_server_exceptions=False)

# --- Tests ---

def test_redact_sensitive_data():
    mock_settings = json.dumps({
        "brave_api_key": "SECRET_BRAVE_123",
        "tavily_api_key": "SECRET_TAVILY_456"
    })
    test_payload = {"messages": [{"id": "msg_1", "content": "Key: SECRET_BRAVE_123.", "metadata": {"used_key": "SECRET_TAVILY_456"}}]}

    with patch("builtins.open", mock_open(read_data=mock_settings)):
        result = services.trace_export.redact_sensitive_data(test_payload)

    assert "[REDACTED]" in result["messages"][0]["content"]
    assert result["messages"][0]["metadata"]["used_key"] == "[REDACTED]"

def test_build_trace_records_owner_mismatch():
    db_mock = MagicMock()
    mock_session_row = MagicMock()
    mock_session_row.owner = "hacker"
    db_mock.query.return_value.filter.return_value.first.return_value = mock_session_row

    result = services.trace_export.build_trace_records(db_mock, "admin", ["msg_1"], "session_1", "success")
    assert result is None

def test_build_trace_records_rejects_partial_export():
    db_mock = MagicMock()
    mock_session_row = MagicMock()
    mock_session_row.owner = "admin"
    db_mock.query.return_value.filter.return_value.first.return_value = mock_session_row
    db_mock.query.return_value.filter.return_value.order_by.return_value.all.return_value = [MagicMock(id="msg_1")]

    with pytest.raises(ValueError, match="Mismatch"):
        services.trace_export.build_trace_records(db_mock, "admin", ["msg_1", "msg_2"], "session_1", "success")

@patch("services.trace_export.build_trace_records")
def test_export_trace_route_success(mock_build_trace):
    mock_build_trace.return_value = {"id": "session_1", "messages": []}
    
    payload = {"session_id": "session_1", "message_ids": ["msg_1"], "label": "success"}
    response = client.post("/api/trace/export", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "success"

@patch("services.trace_export.build_trace_records")
def test_export_trace_route_handles_validation_error(mock_build_trace):
    mock_build_trace.side_effect = ValueError("Mismatch: Some requested messages do not belong")

    payload = {"session_id": "session_1", "message_ids": ["msg_1"], "label": "success"}
    response = client.post("/api/trace/export", json=payload)

    # The exception handler will catch it and return a pristine 400!
    assert response.status_code == 400
    assert "Mismatch" in response.json()["detail"]
