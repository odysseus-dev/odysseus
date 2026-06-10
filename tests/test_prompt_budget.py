from src.prompt_budget import (
    PromptBudgetSection,
    build_prompt_budget_report,
    prompt_message_sections,
)


def test_prompt_budget_report_totals_percentages_sorting_and_empty_sections():
    report = build_prompt_budget_report(
        [
            PromptBudgetSection("large", "base", "a" * 100),
            PromptBudgetSection("empty_optional", "context", ""),
            PromptBudgetSection("medium", "mcp", "b" * 20, item_count=2),
        ],
        largest_limit=2,
    )

    assert report["total"] == {"char_count": 120, "estimated_tokens": 36}

    rows = {row["name"]: row for row in report["sections"]}
    assert rows["large"]["estimated_tokens"] == 30
    assert rows["large"]["percent_of_total"] == 83.33
    assert rows["medium"]["estimated_tokens"] == 6
    assert rows["medium"]["percent_of_total"] == 16.67
    assert rows["medium"]["item_count"] == 2
    assert rows["empty_optional"]["estimated_tokens"] == 0
    assert rows["empty_optional"]["percent_of_total"] == 0.0

    assert [row["name"] for row in report["largest"]] == ["large", "medium"]


def test_prompt_budget_report_omits_raw_section_text():
    report = build_prompt_budget_report(
        [PromptBudgetSection("memory_context", "memory_context", "SECRET_USER_MEMORY")]
    )

    assert "SECRET_USER_MEMORY" not in str(report)
    assert report["sections"][0]["name"] == "memory_context"
    assert report["sections"][0]["category"] == "memory_context"


def test_prompt_message_sections_classify_context_sources_without_leaking_text():
    messages = [
        {"role": "system", "content": "BASE_SYSTEM_PROMPT"},
        {
            "role": "user",
            "content": "SECRET_MEMORY_TEXT",
            "metadata": {"trusted": False, "source": "saved memories"},
        },
        {
            "role": "user",
            "content": "SECRET_DOC_TEXT",
            "metadata": {"trusted": False, "source": "active editor document"},
        },
        {
            "role": "user",
            "content": "SECRET_SKILL_TEXT",
            "metadata": {"trusted": False, "source": "skills"},
        },
    ]

    report = build_prompt_budget_report(prompt_message_sections(messages))
    categories = {row["category"] for row in report["sections"]}

    assert {
        "system_message",
        "memory_context",
        "document_context",
        "skill_context",
    } <= categories
    assert "SECRET_MEMORY_TEXT" not in str(report)
    assert "SECRET_DOC_TEXT" not in str(report)
    assert "SECRET_SKILL_TEXT" not in str(report)
