from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOGIN_HTML = (ROOT / "static" / "login.html").read_text(encoding="utf-8")


def test_embedded_hosts_cannot_render_login_script_source_as_page_content():
    assert "script, style, template { display: none !important; }" in LOGIN_HTML
    assert LOGIN_HTML.index("script, style, template { display: none !important; }") < LOGIN_HTML.index("</style>")
