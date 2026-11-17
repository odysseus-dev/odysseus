import json

import pytest

from src.deep_research import DeepResearcher
from src.research_navigator import ResearchPage


@pytest.mark.parametrize("question,text", [
    ("日本の人工知能", "日本の人工知能の研究について"),
    ("日本の人工知能", "Artificial intelligence research in Japan"),
    ("artificial intelligence", "人工知能の研究について"),
    ("???", "A source that the model can assess"),
])
def test_lexical_filter_defers_when_it_cannot_assess_language(question, text):
    assert DeepResearcher._topic_relevant(question, text)


def test_small_model_filter_still_rejects_unrelated_english():
    assert not DeepResearcher._topic_relevant("Boston Terrier neurology", "Boston tourism and hotels")


@pytest.mark.asyncio
async def test_small_model_can_recover_topic_from_browser(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    researcher = DeepResearcher(llm_endpoint="http://local.test/v1", llm_model="test-9b")
    calls = []

    async def fetch(url, **kwargs):
        return ResearchPage(url=url, title="Welcome", content="Sign in and accept cookies", success=True, retrieval="fetch")

    async def browser_read(url, **kwargs):
        calls.append(url)
        return ResearchPage(url=url, title="Boston Terrier neurology", content="Boston Terrier neurological research findings", success=True, retrieval="browser")

    async def llm(*args, **kwargs):
        return json.dumps({"summary": "Boston Terrier neurological research findings", "evidence": "Boston Terrier neurological research findings"})

    researcher.navigator.fetch = fetch
    researcher.navigator.browser_read = browser_read
    researcher._llm = llm
    result = await researcher._fetch_and_extract("https://example.test/article", "Boston Terrier neurology", "Welcome")
    assert calls == ["https://example.test/article"]
    assert result and result["retrieval"] == "browser"
