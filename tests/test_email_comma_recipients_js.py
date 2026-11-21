"""Exercise actual email UI helpers under Node; no copied parser in the test."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def run(js):
    result = subprocess.run(
        ["node", "--input-type=module"], input=js, text=True, capture_output=True,
        cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(("raw", "expected"), [
    ('"Doe, Jane" <jane@example.test>, bob@example.test', "Doe, Jane, bob"),
    ('"Doe, Jane" <jane@example.test>, bob@example.test, sam@example.test', "Doe, Jane, bob +1"),
    ('"Doe, Jane" <jane@example.test>', "Doe, Jane"),
    ("alice@example.test, bob@example.test, carol@example.test", "alice, bob +1"),
    ("", ""),
])
def test_recipient_summary_counts_addresses_not_name_commas(raw, expected):
    assert run(f"""
        import {{ _formatRecipients }} from './static/js/emailLibrary/utils.js';
        console.log(JSON.stringify(_formatRecipients({json.dumps(raw)})));
    """) == expected


def test_reply_all_excludes_self_without_leaving_a_display_name_fragment():
    assert run("""
        import { buildReplyAllCc } from './static/js/emailLibrary/replyRecipients.js';
        console.log(JSON.stringify(buildReplyAllCc({
            to: '\"Doe, Jane\" <me@example.test>, bob@example.test',
            cc: '\"Smith, Sam\" <sam@example.test>'
        }, 'me@example.test')));
    """) == 'bob@example.test, "Smith, Sam" <sam@example.test>'


@pytest.mark.parametrize("raw,expected", [
    (' , "Doe, Jane" <jane@example.test>, , bob@example.test, ',
     ['"Doe, Jane" <jane@example.test>', 'bob@example.test']),
    ('"last,first"@example.test, bob@example.test',
     ['"last,first"@example.test', 'bob@example.test']),
    (r'"Name\\" <one@example.test>, two@example.test',
     [r'"Name\\" <one@example.test>', 'two@example.test']),
    (r'"Doe, \"Jane\"" <jane@example.test>, bob@example.test',
     [r'"Doe, \"Jane\"" <jane@example.test>', 'bob@example.test']),
])
def test_shared_splitter_keeps_quoted_addresses_and_escaped_quotes(raw, expected):
    assert run(f"""
        import {{ _splitRecipientList }} from './static/js/emailLibrary/utils.js';
        console.log(JSON.stringify(_splitRecipientList({json.dumps(raw)})));
    """) == expected
