from src import agent_loop as al


def _domains(prompt: str) -> set[str]:
    return al._classify_agent_request(
        [{"role": "user", "content": prompt}], prompt
    )["domains"]


def test_local_fireworks_artifact_is_not_model_serving():
    prompt = """Read /workspace/fixtures/config.json, then create
    /workspace/output.html. Launch animated fireworks when the countdown ends."""

    domains = _domains(prompt)

    assert "files" in domains
    assert "cookbook" not in domains


def test_local_news_video_is_not_web_or_personal_task_management():
    prompt = """There is a video at /workspace/fixtures/video.mp4.
    Complete this task: count every distinct news segment in the local video."""

    domains = _domains(prompt)

    assert "files" in domains
    assert "web" not in domains
    assert "notes_calendar_tasks" not in domains


def test_explicit_model_serving_still_routes_cookbook():
    assert "cookbook" in _domains("Launch the Qwen model server from my saved preset.")


def test_explicit_personal_task_list_still_routes_tasks():
    assert "notes_calendar_tasks" in _domains("Show my task list.")


def test_explicit_web_lookup_with_local_input_still_routes_web():
    prompt = "Read /workspace/topic.txt, then search the web for the latest news about it."

    assert "web" in _domains(prompt)
