import pytest
import json
from unittest.mock import patch, mock_open, MagicMock
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from typing import Literal, List, Optional
from pydantic import BaseModel

# Reference the module directly to prevent un-patchable snapshot copies
import services.trace_export

# --- App Setup ---
app = FastAPI()

class TraceExportRequest(BaseModel):
    session_id: str
    message_ids: List[str]
    label: Literal["success", "failed", "needs_review"]
    note: Optional[str] = None

@app.exception_handler(PermissionError)
async def permission_error_handler(request, exc):
    return JSONResponse(status_code=403, content={"detail": str(exc)})

@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

@app.post("/api/trace/export")
def mock_export_endpoint(payload: TraceExportRequest):
    data = services.trace_export.build_trace_records(
        db=None, 
        current_user="admin",  
        message_ids=payload.message_ids, 
        session_id=payload.session_id, 
        label=payload.label,
        note=payload.note
    )
    return {"status": "success", "data": data}

@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)

# --- Tests ---

def test_redact_sensitive_data():
    # FIX: Replaced high-entropy string tokens with repetitive dummy text.
    # This prevents Gitleaks from mistaking our test inputs for real keys.
    mock_settings = json.dumps({
        "brave_api_key": "brave-dummy-key-value-abcde",
        "tavily_api_key": "tavily-dummy-key-value-abcde"
    })
    
    test_payload = {
        "messages": [
            {
                "id": "msg_1", 
                "content": "Check out my key brave-dummy-key-value-abcde or log in via Authorization: Bearer mocktokenvalue11111.", 
                "metadata": {
                    "used_key": "tavily-dummy-key-value-abcde",
                    "local_url": "http://localhost:8000/api/v1/internal",
                    "log_path": "C:\\Users\\Admin\\AppData\\local\\temp_log.txt"
                }
            }
        ]
    }

    with patch("builtins.open", mock_open(read_data=mock_settings)):
        result = services.trace_export.redact_sensitive_data(test_payload)

    scrubbed_msg = result["messages"][0]

    # 1. Assert exact settings keys were redacted
    assert "brave-dummy-key-value-abcde" not in scrubbed_msg["content"]
    assert "tavily-dummy-key-value-abcde" not in scrubbed_msg["metadata"]["used_key"]
    
    # 2. Assert broad generic DLP patterns were successfully redacted
    assert "mocktokenvalue11111" not in scrubbed_msg["content"]
    assert "localhost:8000" not in scrubbed_msg["metadata"]["local_url"]
    assert "AppData" not in scrubbed_msg["metadata"]["log_path"]
    
    # 3. Assert all replacements converted nicely into placeholders
    assert "[REDACTED]" in scrubbed_msg["content"]
    assert scrubbed_msg["metadata"]["used_key"] == "[REDACTED]"
    assert scrubbed_msg["metadata"]["local_url"] == "[REDACTED]"
    assert scrubbed_msg["metadata"]["log_path"] == "[REDACTED]"

def test_build_trace_records_owner_mismatch():
    db_mock = MagicMock()
    mock_session_row = MagicMock()
    mock_session_row.owner = "hacker"
    db_mock.query.return_value.filter.return_value.first.return_value = mock_session_row

    with pytest.raises(PermissionError, match="permission to access this session"):
        services.trace_export.build_trace_records(db_mock, "admin", ["msg_1"], "session_1", "success")

def test_build_trace_records_rejects_partial_export():
    db_mock = MagicMock()
    mock_session_row = MagicMock()
    mock_session_row.owner = "admin"
    db_mock.query.return_value.filter.return_value.first.return_value = mock_session_row
    db_mock.query.return_value.filter.return_value.order_by.return_value.all.return_value = [MagicMock(id="msg_1")]

    with pytest.raises(ValueError, match="Mismatch"):
        services.trace_export.build_trace_records(db_mock, "admin", ["msg_1", "msg_2"], "session_1", "success")

@patch("services.trace_export.build_trace_records")
def test_export_trace_route_success(mock_build_trace, client):
    mock_build_trace.return_value = {"id": "session_1", "messages": []}
    
    payload = {"session_id": "session_1", "message_ids": ["msg_1"], "label": "success"}
    response = client.post("/api/trace/export", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "success"

@patch("services.trace_export.build_trace_records")
def test_export_trace_route_handles_validation_error(mock_build_trace, client):
    mock_build_trace.side_effect = ValueError("Mismatch: Some requested messages do not belong")

    payload = {"session_id": "session_1", "message_ids": ["msg_1"], "label": "success"}
    response = client.post("/api/trace/export", json=payload)

    assert response.status_code == 400
    assert "Mismatch" in response.json()["detail"]

def test_export_trace_route_rejects_invalid_label(client):
    payload = {
        "session_id": "session_1",
        "message_ids": ["msg_1"],
        "label": "unsupported_label_value"
    }
    response = client.post("/api/trace/export", json=payload)

    assert response.status_code == 422