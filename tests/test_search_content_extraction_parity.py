"""Content extraction behavior for the canonical services.search.content module."""

import httpx
import pytest

pytest.importorskip("bs4")

from services.search import content as service_content


class _FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}
    content = b""

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeErrorResponse:
    """Mimics an httpx.Response that fails raise_for_status with a given status code."""

    headers = {"Content-Type": "text/html; charset=utf-8"}
    content = b""
    text = ""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        raise httpx.HTTPStatusError(
            f"{self.status_code} error", request=None, response=self
        )


@pytest.mark.parametrize('markup', [
    '<main><article><h2><a href="/one">First story</a></h2><p>3 points</p></article><article><h2><a href="/two">Second story</a></h2><p>900 points</p></article></main>',
    '<body><table><tr><td>1.</td><td><a href="/vote"><img src="up.png"></a></td><td><a href="/one">First story</a></td></tr><tr><td>3 points</td></tr><tr><td>2.</td><td><a href="/two">Second story</a></td></tr><tr><td>900 points</td></tr></table></body>',
    '<main><ol><li><a href="/one">First story</a> 3 points</li><li><a href="/two">Second story</a> 900 points</li></ol></main>',
])
def test_listing_links_preserve_destination_and_source_order(markup, tmp_path, monkeypatch):
    monkeypatch.setattr(service_content, 'CONTENT_CACHE_DIR', tmp_path)
    monkeypatch.setattr(service_content, '_get_public_url', lambda *a, **k: _FakeResponse(markup))
    result = service_content.fetch_webpage_content('https://example.org/list')
    text = result['linked_content']
    assert '[First story](<https://example.org/one>)' in text
    assert '[Second story](<https://example.org/two>)' in text
    assert text.index('First story') < text.index('Second story')
    assert '\n' in text
    assert '](' not in result['content']
    assert result['page_entries'] == [
        {'title': 'First story', 'url': 'https://example.org/one'},
        {'title': 'Second story', 'url': 'https://example.org/two'},
    ]


def test_link_extraction_ignores_unsafe_schemes_and_escapes_labels():
    from bs4 import BeautifulSoup
    html = '<main><a href="javascript:alert(1)">Bad</a><a href="#part">Jump</a><a href="/a?q=1&amp;b=2">A [label]</a></main>'
    text = service_content._linked_text(BeautifulSoup(html, 'html.parser'), 'https://example.org/')
    assert 'javascript:' not in text
    assert '[Jump]' not in text
    assert r'[A \[label\]](<https://example.org/a?q=1&b=2>)' in text


@pytest.mark.asyncio
async def test_web_fetch_delivers_linked_content_to_model(monkeypatch):
    from src.agent_tools.web_tools import WebFetchTool
    from src.search import content
    linked = '[First story](<https://example.org/one>)\n[Second story](<https://example.org/two>)'
    monkeypatch.setattr(content, 'fetch_webpage_content', lambda *a, **k: {
        'title': 'Listing', 'content': 'First story Second story', 'linked_content': linked,
    })
    result = await WebFetchTool().execute('{"url":"https://example.org/list"}', {})
    assert result['exit_code'] == 0
    assert linked in result['output']


def test_simple_listing_is_rendered_with_links_without_reranking():
    from src.clean_agent_preview import page_listing_response
    entries = [{'title': 'Low points first', 'url': 'https://example.org/a'},
               {'title': 'High points second', 'url': 'https://example.org/b'}]
    text = page_listing_response(entries, 'top hackernews stories')
    assert '1. [Low points first](<https://example.org/a>)' in text
    assert '2. [High points second](<https://example.org/b>)' in text
    assert page_listing_response(entries, 'Summarize the articles') == ''
    assert page_listing_response(entries, 'top stories and email them to Jon') == ''
    assert page_listing_response(entries, 'top stories about science') == ''


def test_single_article_inside_main_excludes_related_links_and_comment_form(tmp_path, monkeypatch):
    body = 'The measured storage comparison includes uncertainty and cost limitations. ' * 8
    html = f'<main><article><h1>Storage comparison</h1><p>{body}</p></article><section>Related posts: home battery storage tags</section><form>Leave a comment</form></main>'
    monkeypatch.setattr(service_content, 'CONTENT_CACHE_DIR', tmp_path)
    monkeypatch.setattr(service_content, '_get_public_url', lambda *a, **k: _FakeResponse(html))
    result = service_content.fetch_webpage_content('https://example.org/article')
    assert body.strip() in result['content']
    assert 'Related posts' not in result['content']
    assert 'Leave a comment' not in result['content']


def test_multiple_article_cards_retain_main_context(tmp_path, monkeypatch):
    html = '<main><h1>Search results for storage</h1><article>First result</article><article>Second result</article></main>'
    monkeypatch.setattr(service_content, 'CONTENT_CACHE_DIR', tmp_path)
    monkeypatch.setattr(service_content, '_get_public_url', lambda *a, **k: _FakeResponse(html))
    result = service_content.fetch_webpage_content('https://example.org/listing')
    assert 'Search results for storage' in result['content']
    assert 'First result' in result['content'] and 'Second result' in result['content']


@pytest.mark.parametrize('title,body,blocked', [
    ('Client Challenge', 'A required part of this site couldn’t load. Try using a different browser.', True),
    ('Just a moment...', 'Checking your browser. Enable JavaScript to continue.', True),
    ('Understanding browser challenges', 'Checking your browser is a common security message.', False),
    ('Client Challenge', 'An article about designing client challenges for a programming exercise.', False),
    ('Client Challenge', 'A required part of this site ' + 'substantive discussion ' * 150, False),
])
def test_access_interstitial_is_not_article_evidence(title, body, blocked, tmp_path, monkeypatch):
    monkeypatch.setattr(service_content, 'CONTENT_CACHE_DIR', tmp_path)
    html = f'<html><title>{title}</title><body><main>{body}</main></body></html>'
    monkeypatch.setattr(service_content, '_get_public_url', lambda *a, **k: _FakeResponse(html))
    result = service_content.fetch_webpage_content('https://example.com/challenge')
    assert result['success'] is not blocked
    if blocked:
        assert result['content'] == ''
        assert result['error_kind'] == 'access_challenge'
        assert 'private_browser' in result['error']
        assert not list(tmp_path.iterdir()), 'Transient challenge must not be cached as article content'
    else:
        assert body.strip() in result['content']


@pytest.mark.parametrize('wrapper', ['main', 'article', 'div class="content"'])
def test_extraction_does_not_repeat_nested_content_or_include_navigation(wrapper, tmp_path, monkeypatch):
    closing = wrapper.split()[0]
    html = (f'<html><body><nav>{"Navigation item " * 100}</nav><{wrapper}>'
            '<header>Article title and publication date</header>'
            '<div class="article-body"><div class="entry-content">'
            '<p>Unique substantive evidence.</p></div></div>'
            f'</{closing}><footer>Unrelated links</footer></body></html>')
    monkeypatch.setattr(service_content, 'CONTENT_CACHE_DIR', tmp_path)
    monkeypatch.setattr(service_content, '_get_public_url', lambda *a, **k: _FakeResponse(html))
    result = service_content.fetch_webpage_content('https://example.com/nested')
    assert result['content'].count('Unique substantive evidence.') == 1
    assert 'Navigation item' not in result['content']
    assert 'Unrelated links' not in result['content']
    assert 'Article title' in result['content']


@pytest.mark.parametrize("module", [service_content])
def test_content_fetcher_extracts_og_image_and_body_fallback(module, tmp_path, monkeypatch):
    html = """
    <html>
      <head>
        <title>Example</title>
        <meta property="og:image" content="https://example.com/cover.jpg">
      </head>
      <body>
        <nav>Navigation text should not win</nav>
        <div class="content">Tiny</div>
        <main>
          <p>This is the substantive body text that should be retained.</p>
          <p>It is much longer than the tiny class-matched wrapper.</p>
        </main>
        <script>window.secret = "not content";</script>
      </body>
    </html>
    """

    monkeypatch.setattr(module, "CONTENT_CACHE_DIR", tmp_path)
    module.content_cache_index.clear()
    monkeypatch.setattr(module, "_get_public_url", lambda url, headers, timeout, **kwargs: _FakeResponse(html))

    result = module.fetch_webpage_content("https://example.com/parity-test")

    assert result["og_image"] == "https://example.com/cover.jpg"
    assert "substantive body text" in result["content"]
    assert "much longer than the tiny" in result["content"]
    assert "window.secret" not in result["content"]


@pytest.mark.parametrize("status_code", [403, 404])
def test_fetch_webpage_content_returns_empty_result_on_http_status_error(status_code, tmp_path, monkeypatch):
    """A 403/404 response should degrade to an empty result instead of raising.

    This exercises the real fetch_webpage_content() path: _get_public_url returns
    a response whose raise_for_status() raises httpx.HTTPStatusError, and the
    function must catch it and hand back the standard empty-result shape rather
    than letting the exception bubble up (which previously surfaced as a 500).
    """
    monkeypatch.setattr(service_content, "CONTENT_CACHE_DIR", tmp_path)
    service_content.content_cache_index.clear()
    monkeypatch.setattr(
        service_content,
        "_get_public_url",
        lambda url, headers, timeout, **kwargs: _FakeErrorResponse(status_code),
    )

    result = service_content.fetch_webpage_content(f"https://example.com/status-{status_code}")

    assert result["success"] is False
    assert result["content"] == ""
    assert str(status_code) in result["error"]


def test_fetch_webpage_content_429_takes_distinct_rate_limit_path(tmp_path, monkeypatch):
    """A 429 response must be handled by the dedicated rate-limit branch.

    The status_code == 429 check runs before raise_for_status() is ever called,
    so a 429 should be reported as a rate-limit error rather than falling through
    the generic HTTPStatusError handling added for 403/404. We assert on the
    error message to prove it took the RateLimitError path, not the HTTP-status
    empty-result path.
    """
    monkeypatch.setattr(service_content, "CONTENT_CACHE_DIR", tmp_path)
    service_content.content_cache_index.clear()

    raise_for_status_called = False

    class _FakeRateLimitResponse:
        status_code = 429
        headers = {"Content-Type": "text/html; charset=utf-8"}
        content = b""
        text = ""

        def raise_for_status(self):
            nonlocal raise_for_status_called
            raise_for_status_called = True

    monkeypatch.setattr(
        service_content,
        "_get_public_url",
        lambda url, headers, timeout, **kwargs: _FakeRateLimitResponse(),
    )

    result = service_content.fetch_webpage_content("https://example.com/rate-limited")

    assert result["success"] is False
    assert result["content"] == ""
    assert "Rate limit hit" in result["error"]
    assert "HTTP 429" not in result["error"]
    # The 429 short-circuit must happen before raise_for_status() is reached.
    assert raise_for_status_called is False
