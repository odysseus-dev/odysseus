from src.tool_execution import format_tool_result


def test_binary_like_terminal_output_is_replaced_with_extraction_guidance():
    binary_dump = "PK" + ("\ufffd\x00\x01" * 1000)

    rendered = format_tool_result(
        "bash: cat report.docx",
        {"output": binary_dump, "exit_code": 0},
    )

    assert "Binary-like output omitted" in rendered
    assert "format-specific extractor" in rendered
    assert binary_dump not in rendered


def test_non_ascii_text_output_is_preserved_when_it_is_not_binary_like():
    text = "诉讼时效期间为三年。"

    rendered = format_tool_result("python", {"output": text, "exit_code": 0})

    assert text in rendered
    assert "Binary-like output omitted" not in rendered
