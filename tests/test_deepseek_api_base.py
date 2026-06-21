from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deepseek_setup_surfaces_use_current_api_root():
    index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    admin_js = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    slash_commands = (ROOT / "static" / "js" / "slashCommands.js").read_text(encoding="utf-8")
    webhook_routes = (ROOT / "routes" / "webhook_routes.py").read_text(encoding="utf-8")

    assert 'value="https://api.deepseek.com"' in index_html
    assert "if (host === 'api.deepseek.com')" in admin_js
    assert "deepseek: { name: 'DeepSeek', url: 'https://api.deepseek.com' }" in slash_commands
    assert '"deepseek": "https://api.deepseek.com"' in webhook_routes
    assert "https://api.deepseek.com/v1" not in index_html
    assert "https://api.deepseek.com/v1" not in slash_commands


def test_android_deepseek_normalizer_collapses_legacy_v1_base():
    server = (
        ROOT
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "odysseus"
        / "simplesignal"
        / "MobileBackendServer.java"
    ).read_text(encoding="utf-8")

    assert '"https://api.deepseek.com/v1".equals(lower)' in server
    assert 'return "https://api.deepseek.com";' in server
    assert 'return url + "/v1";' in server
