"""Unauthenticated browser hits must keep the original query after login."""

from urllib.parse import quote

from core.middleware import login_redirect_url, safe_post_login_path


def test_safe_post_login_path_keeps_agents_query():
    assert safe_post_login_path("/?agents=1") == "/?agents=1"


def test_safe_post_login_path_rejects_open_redirects():
    assert safe_post_login_path("https://evil.example/") == "/"
    assert safe_post_login_path("//evil.example/") == "/"
    assert safe_post_login_path("/\\evil") == "/"
    assert safe_post_login_path("/login") == "/"
    assert safe_post_login_path("/login?next=/notes") == "/"


def test_login_redirect_url_preserves_application_query():
    location = login_redirect_url(
        {
            "root_path": "",
            "path": "/",
            "query_string": b"agents=1",
        }
    )
    assert location == f"/login?next={quote('/?agents=1', safe='')}"


def test_login_redirect_url_includes_asgi_root_path():
    location = login_redirect_url(
        {
            "root_path": "/odysseus",
            "path": "/odysseus/notes",
            "query_string": b"",
        }
    )
    assert location == f"/odysseus/login?next={quote('/notes', safe='')}"
