"""Test that backup import/export for Deep Research runs works correctly."""
import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock
import routes.backup_routes as br

class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

def test_research_export(monkeypatch, tmp_path):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")
    
    # Mock research directory
    research_dir = tmp_path / "data" / "deep_research"
    research_dir.mkdir(parents=True)
    
    # Create a mock research run
    run_data = {"owner": "alice", "query": "test query"}
    with open(research_dir / "run1.json", "w") as f:
        json.dump(run_data, f)
        
    # Mock other runs (different owner)
    with open(research_dir / "run2.json", "w") as f:
        json.dump({"owner": "bob", "query": "other query"}, f)
        
    # Override Path in backup_routes to use tmp_path
    monkeypatch.setattr(br, "Path", lambda *args: tmp_path / Path(*args) if "data" in str(args) else Path(*args))
    
    mem_manager = MagicMock()
    mem_manager.load.return_value = []
    preset_manager = MagicMock()
    preset_manager.get_all.return_value = {}
    skills_manager = MagicMock()
    skills_manager.load.return_value = []
    
    router = br.setup_backup_routes(mem_manager, preset_manager, skills_manager)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/export")
    
    response = asyncio.run(endpoint(None))
    body = json.loads(response.body)
    
    assert "research" in body
    assert len(body["research"]) == 1
    assert body["research"][0]["id"] == "run1"
    assert body["research"][0]["data"]["query"] == "test query"

def test_research_import(monkeypatch, tmp_path):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")
    
    research_dir = tmp_path / "data" / "deep_research"
    # Ensure it's clean
    if research_dir.exists():
        shutil.rmtree(research_dir)
        
    monkeypatch.setattr(br, "Path", lambda *args: tmp_path / Path(*args) if "data" in str(args) else Path(*args))
    
    router = br.setup_backup_routes(MagicMock(), MagicMock(), MagicMock())
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/import")
    
    body = {
        "research": [
            {
                "id": "new_run",
                "data": {"query": "imported query"}
            }
        ]
    }
    
    asyncio.run(endpoint(_Req(body)))
    
    target_file = research_dir / "new_run.json"
    assert target_file.exists()
    with open(target_file, "r") as f:
        data = json.load(f)
    assert data["query"] == "imported query"
    assert data["owner"] == "alice"
