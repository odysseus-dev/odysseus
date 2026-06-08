from src.memory import MemoryManager


def test_extract_memory_from_chat_skips_non_dict_messages(tmp_path):
    # chat_history rows can be malformed (a non-dict slipping in from a partial
    # session blob); the old loop did msg.get(...) and crashed on the first one.
    m = MemoryManager(str(tmp_path))
    history = [
        {"role": "assistant", "content": "- remember to buy milk"},
        "junk-msg",
        None,
        {"role": "user", "content": "hi"},
    ]
    out = m.extract_memory_from_chat(history)
    assert any(e["text"] == "remember to buy milk" for e in out)
