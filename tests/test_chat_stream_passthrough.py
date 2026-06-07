from routes import chat_routes


def test_chat_mode_forwards_model_waiting_status_events():
    assert chat_routes._is_chat_mode_forward_only_event({"type": "model_waiting"})


def test_agent_mode_forwards_model_waiting_status_events():
    assert chat_routes._is_agent_mode_forward_only_event({"type": "model_waiting"})


def test_delta_events_are_not_forward_only_status_events():
    event = {"delta": "hello"}

    assert not chat_routes._is_chat_mode_forward_only_event(event)
    assert not chat_routes._is_agent_mode_forward_only_event(event)
