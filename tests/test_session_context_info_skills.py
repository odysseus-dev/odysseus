from routes.session_routes import _context_info_skill_inventory


class FakeSkillsManager:
    def index_for(self, owner=None, **_kwargs):
        assert owner == "alice"
        return [
            {
                "name": "openai-docs",
                "description": "Use official OpenAI docs.",
                "category": "system",
                "status": "published",
            }
        ]

    def load(self, owner=None):
        assert owner == "alice"
        return [
            {
                "name": "openai-docs",
                "description": "Use official OpenAI docs.",
                "path": "/skills/openai-docs/SKILL.md",
                "procedure": "Full body should not be exposed in context_info.",
            }
        ]


def test_context_info_skill_inventory_is_compact_owner_scoped_metadata():
    skills = _context_info_skill_inventory(FakeSkillsManager(), owner="alice")

    assert skills == [
        {
            "name": "openai-docs",
            "description": "Use official OpenAI docs.",
            "source": "file: /skills/openai-docs/SKILL.md",
        }
    ]
    assert "procedure" not in skills[0]


def test_context_info_skill_inventory_tolerates_missing_manager():
    assert _context_info_skill_inventory(None, owner="alice") == []
