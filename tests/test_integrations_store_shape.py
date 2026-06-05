import json

from src import integrations


def test_load_integrations_skips_non_object_rows(tmp_path, monkeypatch):
    data_file = tmp_path / "integrations.json"
    data_file.write_text(json.dumps([{"id": "good", "name": "Good"}, "bad", None]))
    monkeypatch.setattr(integrations, "DATA_FILE", str(data_file))

    assert integrations.load_integrations() == [{"id": "good", "name": "Good"}]
