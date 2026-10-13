from src.clean_agent_preview import compact_schemas
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


def test_compact_pdf_contract_explains_visual_figure_recovery():
    schemas = {
        item["function"]["name"]: item["function"]
        for item in compact_schemas(FUNCTION_TOOL_SCHEMAS)
    }

    description = schemas["pdf_extract"]["description"]
    assert "inspect_media" in description
    assert "pages" in description
    assert "figure" in description
