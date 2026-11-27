"""Literal CLI substring searches must not interpret SQL pattern characters."""
import json
from datetime import datetime, timedelta

import pytest

from core.database import GalleryImage, Note
from tests.helpers.cli_loader import load_script
from tests.helpers.database import disposable_database


RECORDS = [
    ("plain", "Plain record", "plain", ""),
    ("percent", "100% linen", "fabric", ""),
    ("percent-decoy", "100 percent linen", "cloth", ""),
    ("under", "project_alpha", "sample", ""),
    ("under-decoy", "projectXalpha", "sample", ""),
    ("slash", "Path sample", r"C:\photos\_drafts\100%", ""),
    ("combined", "Sale notes", "sale_50%", ""),
    ("slash-decoy", "Another path", r"C:\photos\drafts\100 percent", ""),
    ("ai-only", "Auto labels", "neutral", "ai_75%"),
    ("inactive", "Archived_100%", "other", ""),
]


@pytest.fixture(params=["notes", "gallery"])
def search_cli(request, tmp_path, monkeypatch):
    feature = request.param
    with disposable_database(tmp_path) as factory:
        with factory() as db:
            for index, (uid, title, text, ai_tags) in enumerate(RECORDS):
                stamp = datetime(2026, 1, 1) + timedelta(minutes=index)
                if feature == "notes":
                    row = Note(id=uid, title=title, content=" ".join([text, ai_tags]),
                               archived=uid == "inactive", created_at=stamp, updated_at=stamp)
                else:
                    row = GalleryImage(id=uid, filename=uid + ".png", prompt=title,
                                       tags=text, ai_tags=ai_tags, is_active=uid != "inactive",
                                       created_at=stamp, updated_at=stamp)
                db.add(row)
            db.commit()
        cli = load_script("odysseus-" + feature)
        monkeypatch.setattr(cli, "SessionLocal", factory)
        yield feature, cli


@pytest.mark.parametrize("query,limit", [
    ("%", 50), ("_", 50), ("100%", 50), ("project_alpha", 50),
    (r"\_", 50), (r"\%", 50), (r"C:\photos", 50),
    ("LINEN", 50), ("missing", 50), ("", 50), ("ai_75%", 50),
    ("%", 1), ("project_alpha", 1),
], ids=["percent", "underscore", "suffix-percent", "embedded-underscore",
        "backslash-underscore", "backslash-percent", "path", "ordinary-case-insensitive",
        "no-match", "empty", "ai-tags", "limited-percent", "limited-underscore"])
def test_search_is_a_literal_substring_with_existing_order_and_limit(search_cli, capsys, query, limit):
    feature, cli = search_cli
    # Independent literal-substring oracle, over the original input fields.
    expected = [uid for uid, title, text, ai in reversed(RECORDS)
                if (feature == "notes" or uid != "inactive")
                and any(query.lower() in value.lower() for value in (title, text, ai))][:limit]
    args = cli._build_parser().parse_args(["search", query, "--limit", str(limit)])

    args.func(args)

    output = capsys.readouterr()
    assert output.err == ""
    assert [row["id"] for row in json.loads(output.out)] == expected
