"""_parse_extraction_json must survive reasoning-model noise.

The extraction model wraps its JSON array in <think> blocks, ```json fences,
or leading/trailing prose. The helper strips that noise and slices the array
unconditionally — a reply that starts with '[' can still carry trailing
commentary like "[...] Done!" that would otherwise break json.loads.
"""

from services.memory.memory_extractor import _parse_extraction_json


def test_think_prefixed_array_parses_to_one_fact():
    raw = '<think>reasoning...</think>\n[{"text": "x", "category": "fact"}]'
    assert _parse_extraction_json(raw) == [{"text": "x", "category": "fact"}]


def test_fenced_json_block_parses():
    raw = '```json\n[{"text": "x", "category": "fact"}]\n```'
    assert _parse_extraction_json(raw) == [{"text": "x", "category": "fact"}]


def test_leading_prose_before_array_parses():
    raw = 'Here are the durable facts:\n[{"text": "x", "category": "fact"}]'
    assert _parse_extraction_json(raw) == [{"text": "x", "category": "fact"}]


def test_trailing_commentary_after_array_parses():
    # Exercises the unconditional slice: text starts with '[' but has trailing
    # commentary that the old `text[0] != "["` guard skipped, breaking json.loads.
    raw = '[{"text": "x", "category": "fact"}] Done!'
    assert _parse_extraction_json(raw) == [{"text": "x", "category": "fact"}]


def test_malformed_no_array_returns_empty():
    assert _parse_extraction_json("no array here, sorry") == []
    assert _parse_extraction_json("") == []
