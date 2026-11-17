from services.hwfit.hf_discovery import _entry_from_collection_item

_SOURCE = {"owner": "Qwen", "provider": "Qwen"}
_COLLECTION = {"title": "Qwen3.6"}


def _item(repo_id, num_params):
    item = {"id": repo_id, "type": "model", "pipeline_tag": "text-generation"}
    if num_params is not None:
        item["numParameters"] = num_params
    return item


def test_moe_active_size_is_read_from_name_when_hf_reports_total():
    # HF collection items carry numParameters for nearly every repo. The MoE
    # active size only exists in the name, so it must still be parsed.
    entry = _entry_from_collection_item(_COLLECTION, _item("Qwen/Qwen3.6-35B-A3B", 35_951_822_704), _SOURCE)
    assert entry["parameters_raw"] == 35_951_822_704
    assert entry["is_moe"] is True
    assert entry["active_parameters"] == 3_000_000_000


def test_moe_entry_without_hf_count_falls_back_to_name():
    entry = _entry_from_collection_item(_COLLECTION, _item("Qwen/Qwen3-Coder-30B-A3B-Instruct", None), _SOURCE)
    assert entry["parameters_raw"] == 30_000_000_000
    assert entry["is_moe"] is True
    assert entry["active_parameters"] == 3_000_000_000


def test_dense_entry_keeps_hf_count_and_is_not_moe():
    entry = _entry_from_collection_item(_COLLECTION, _item("Qwen/Qwen3.6-27B", 27_781_427_952), _SOURCE)
    assert entry["parameters_raw"] == 27_781_427_952
    assert "is_moe" not in entry
