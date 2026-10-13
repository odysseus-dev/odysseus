from src.clean_agent_preview import successful_duplicate_recovery_message


def test_duplicate_pdf_recovery_names_missing_artifacts_and_next_tools(tmp_path):
    prompt = (
        "Read /workspace/fixtures/paper.pdf and create "
        "/workspace/results.csv and /workspace/chart.png"
    )

    message = successful_duplicate_recovery_message(
        "pdf_extract",
        "withheld for the next correction round",
        prompt,
        str(tmp_path),
        {"url": "/workspace/fixtures/paper.pdf", "query": "Figure 5"},
    )

    assert "/workspace/results.csv" in message
    assert "/workspace/chart.png" in message
    assert "python or write_file" in message
    assert "inspect_media" in message
    assert "pages" in message
