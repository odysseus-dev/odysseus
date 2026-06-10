import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from pathlib import Path
import json

from services.memory.skills import SkillsManager
from routes.skills_routes import setup_skills_routes

def test_upload_skill_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    sm = SkillsManager(str(tmp_path))
    monkeypatch.setattr("routes.skills_routes.get_current_user", lambda _req: "alice")

    app = FastAPI()
    app.include_router(setup_skills_routes(sm))
    client = TestClient(app)

    md_content = """---
name: uploaded-skill
description: Uploaded skill description
version: 1.0.0
category: general
tags: [test]
status: draft
confidence: 0.8
source: learned
owner: alice
---

## When to Use
Trigger text.

## Procedure
1. Step one
"""

    response = client.post(
        "/api/skills/upload",
        files={"file": ("SKILL.md", md_content.encode("utf-8"), "text/markdown")}
    )

    assert response.status_code == 200
    res_json = response.json()
    assert res_json["ok"] is True
    assert res_json["skill"]["name"] == "uploaded-skill"
    assert res_json["skill"]["description"] == "Uploaded skill description"

    # Verify file is on disk
    skills = sm.load(owner="alice")
    assert len(skills) == 1
    assert skills[0]["name"] == "uploaded-skill"
    assert skills[0]["description"] == "Uploaded skill description"


def test_rename_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    sm = SkillsManager(str(tmp_path))
    monkeypatch.setattr("routes.skills_routes.get_current_user", lambda _req: "alice")

    app = FastAPI()
    app.include_router(setup_skills_routes(sm))
    client = TestClient(app)

    # First add a skill
    sm.add_skill(
        name="original-skill",
        description="Original description",
        owner="alice",
        when_to_use="When original",
        procedure=["Step 1"],
    )

    # Update/Rename via PUT
    response = client.put(
        "/api/skills/original-skill",
        json={"name": "renamed-skill"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # Verify original name is gone, and renamed-skill exists
    skills = sm.load(owner="alice")
    assert len(skills) == 1
    assert skills[0]["name"] == "renamed-skill"
    assert skills[0]["description"] == "Original description"
