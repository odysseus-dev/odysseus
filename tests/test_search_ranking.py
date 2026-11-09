from services.search.ranking import rank_search_results


def test_news_queries_prefer_news_sources_over_sports_and_social_results():
    results = [
        {
            "title": "Chicago Stars fire GM Richard Feuz",
            "url": "https://www.reuters.com/sports/soccer/chicago-stars-fire-gm-richard-feuz--flm-2026-05-27/",
            "snippet": "The Chicago Stars fired their general manager.",
        },
        {
            "title": "United States Eliminates Canada In Quarterfinals",
            "url": "https://sports.yahoo.com/articles/united-states-vs-canada-live-updates-170747222.html",
            "snippet": "United States eliminated Canada in hockey.",
        },
        {
            "title": "Canada - AP News",
            "url": "https://apnews.com/hub/canada",
            "snippet": "Stay up to date on the latest Canada news coverage from AP News.",
        },
        {
            "title": "CBC News - Canada",
            "url": "https://www.cbc.ca/news/canada",
            "snippet": "Your source for Canadian news in English.",
        },
        {
            "title": "CTV News - Canada",
            "url": "https://www.ctvnews.ca/canada",
            "snippet": "Latest news, travel, politics, money, jobs and more.",
        },
    ]

    ranked = rank_search_results("Canada news today", results)
    top_urls = [item["url"] for item in ranked[:3]]

    assert "https://apnews.com/hub/canada" in top_urls
    assert "https://www.cbc.ca/news/canada" in top_urls
    assert "https://www.ctvnews.ca/canada" in top_urls
    assert ranked[-1]["url"].startswith("https://sports.yahoo.com/")


def test_software_release_queries_prefer_official_github_releases():
    results = [
        {
            "title": "ollama/ollama on GitHub | Release Alert",
            "url": "https://releasealert.dev/github/ollama/ollama",
            "snippet": "Latest releases for ollama/ollama on GitHub.",
        },
        {
            "title": "Ollama Release Notes - August 2026 Latest Updates",
            "url": "https://releasebot.io/updates/ollama",
            "snippet": "Complete list of Ollama latest updates.",
        },
        {
            "title": "Releases · ollama/ollama - GitHub",
            "url": "https://github.com/ollama/ollama/releases",
            "snippet": "Latest release v0.33.0.",
        },
    ]

    ranked = rank_search_results("latest ollama release version github", results)

    assert ranked[0]["url"] == "https://github.com/ollama/ollama/releases"


def test_product_spec_queries_prefer_official_shop_or_specs_pages():
    results = [
        {
            "title": "Surprise launch coverage for new creator laptop",
            "url": "https://example-news.com/tech/surprise-launch-creator-laptop",
            "snippet": "The laptop was announced yesterday and may ship later this year.",
        },
        {
            "title": "Creator Laptop - Buy",
            "url": "https://www.creatorlaptop.com/shop/buy-laptop",
            "snippet": "Configure with 64GB RAM. See pricing, availability, and shipping options.",
        },
        {
            "title": "Creator Laptop rumors and expected specs",
            "url": "https://rumor.example.com/creator-laptop-expected-specs",
            "snippet": "Rumored future models could include more memory.",
        },
    ]

    ranked = rank_search_results("current creator laptop memory price availability", results)

    assert ranked[0]["url"] == "https://www.creatorlaptop.com/shop/buy-laptop"
