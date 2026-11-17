import json
import subprocess
from pathlib import Path


def test_calendar_location_escapes_markup_surrounding_links():
    source = (Path(__file__).resolve().parents[1] / "static/js/calendar.js").read_text()
    function = source[source.index("function _locHTML("):source.index("// ── Open / Close", source.index("function _locHTML("))]
    script = r'''
const _e = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
''' + function + r'''
const location = '<img src=x onerror=alert(1)> https://example.test/meeting <svg onload=alert(2)>';
console.log(JSON.stringify(_locHTML(location)));
'''
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    html = json.loads(result.stdout)
    assert "<img" not in html
    assert "<svg" not in html
    assert "&lt;img" in html
    assert 'href="https://example.test/meeting"' in html


def test_source_email_is_visible_with_only_one_calendar():
    source = (Path(__file__).resolve().parents[1] / "static/js/calendar.js").read_text()
    function = source[source.index("function _eventSourceHtml("):source.index("async function _fetchEventByUid(")]
    script = "const _e = String; const _calendars = [];\n" + function + "\nconsole.log(_eventSourceHtml({source_email_uid:'42', source_email_folder:'INBOX'}));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert '<svg' in result.stdout
    assert 'href="#email=INBOX:42"' in result.stdout
