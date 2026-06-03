from pathlib import Path


def test_update_database_has_single_main_guard():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text()

    assert text.count('if __name__ == "__main__":') == 1
