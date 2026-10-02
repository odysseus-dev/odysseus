from src.chatgpt_subscription import build_responses_input


def test_build_responses_input_preserves_function_call_and_output():
    messages = [
        {"role": "user", "content": "Read greeting.txt"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"greeting.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "Hello :)\nHow are you?",
        },
    ]

    assert build_responses_input(messages) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Read greeting.txt"},
            ],
        },
        {
            "type": "function_call",
            "call_id": "call_123",
            "name": "read_file",
            "arguments": '{"path":"greeting.txt"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "Hello :)\nHow are you?",
        },
    ]
