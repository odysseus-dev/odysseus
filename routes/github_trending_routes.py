# routes/github_trending_routes.py
# GitHub Trending with SQLite caching + history + AI interpretation.
import json
import logging
import re
import httpx
from datetime import date, datetime
from pathlib import Path
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)
_HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "github-trending.html"

# ── Lazy DB import (avoids circular at module level) ──

def _get_session():
    from core.database import SessionLocal
    return SessionLocal()


def _today_str():
    return date.today().isoformat()


# ── GitHub scraping ──

def _fetch_trending(language: str = "", period: str = "daily") -> list:
    """Fetch + parse GitHub Trending page, return list of repo dicts."""
    if period not in ("daily", "weekly", "monthly"):
        period = "daily"

    if language:
        url = f"https://github.com/trending/{language}?since={period}"
    else:
        url = f"https://github.com/trending?since={period}"

    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Odysseus/1.0)"}
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.warning("github trending fetch failed: %s", e)
        return []

    articles = re.findall(r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
    results = []
    for art in articles[:25]:
        h2 = re.search(r'<h2[^>]*>(.*?)</h2>', art, re.DOTALL)
        repo_path = ""
        if h2:
            h2_links = re.findall(r'href="(/[^"]+/[^"]+)"', h2.group(1))
            for lnk in h2_links:
                if lnk.count('/') == 2 and not any(
                    lnk.endswith(s) for s in ('/stargazers', '/forks', '/network', '/issues', '/pulls')
                ):
                    repo_path = lnk
                    break
        if not repo_path:
            continue

        desc = ""
        desc_match = re.search(r'<p[^>]+class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', art, re.DOTALL)
        if desc_match:
            desc = re.sub(r'<[^>]+>', '', desc_match.group(1))
            desc = re.sub(r'\s+', ' ', desc).strip()

        lang_val = ""
        lang_match = re.search(r'itemprop="programmingLanguage">(.*?)<', art, re.DOTALL | re.IGNORECASE)
        if lang_match:
            lang_val = lang_match.group(1).strip()

        stars_today = ""
        today_match = re.search(r'([\d][\d,]*)\s+stars?\s+(today|this\s+week|this\s+month)', art, re.DOTALL | re.IGNORECASE)
        if today_match:
            stars_today = today_match.group(1).strip()

        total_stars = ""
        stars_match = re.search(r'/stargazers">.*?([\d,]+)\s*<', art, re.DOTALL)
        if stars_match:
            total_stars = stars_match.group(1).strip()

        forks = ""
        forks_match = re.search(r'/forks">.*?([\d,]+)\s*<', art, re.DOTALL)
        if forks_match:
            forks = forks_match.group(1).strip()

        results.append({
            "name": repo_path.lstrip('/'),
            "url": f"https://github.com{repo_path}",
            "desc": desc,
            "lang": lang_val,
            "stars_today": stars_today,
            "total_stars": total_stars,
            "forks": forks,
        })

    return results


# ── Cache helpers ──

def _save_to_cache(period: str, repos: list, snapshot_date: str | None = None):
    """Save trending repos to database."""
    from core.database import GithubTrending
    d = snapshot_date or _today_str()
    session = _get_session()
    try:
        existing = session.query(GithubTrending).filter_by(date=d, period=period).first()
        if existing:
            existing.repos_json = json.dumps(repos, ensure_ascii=False)
            existing.updated_at = datetime.utcnow()
        else:
            row = GithubTrending(date=d, period=period, repos_json=json.dumps(repos, ensure_ascii=False))
            session.add(row)
        session.commit()
    except Exception as e:
        logger.warning("github trending cache save failed: %s", e)
        session.rollback()
    finally:
        session.close()


def _load_from_cache(period: str, snapshot_date: str | None = None):
    """Load cached trending repos. Returns list or None."""
    from core.database import GithubTrending
    d = snapshot_date or _today_str()
    session = _get_session()
    try:
        row = session.query(GithubTrending).filter_by(date=d, period=period).first()
        if row and row.repos_json:
            return json.loads(row.repos_json)
        return None
    except Exception as e:
        logger.warning("github trending cache load failed: %s", e)
        return None
    finally:
        session.close()


# ── AI data helpers ──

def _enrich_with_ai(repos: list) -> list:
    """Attach desc_zh and interpretation from github_trending_repo_ai to each repo."""
    if not repos:
        return repos
    from core.database import GithubTrendingRepoAI
    session = _get_session()
    try:
        names = [r["name"] for r in repos]
        ai_rows = session.query(GithubTrendingRepoAI).filter(
            GithubTrendingRepoAI.repo_name.in_(names)
        ).all()
        ai_map = {row.repo_name: row for row in ai_rows}
        for repo in repos:
            ai = ai_map.get(repo["name"])
            repo["desc_zh"] = ai.desc_zh if ai else None
            repo["interpretation"] = ai.interpretation if ai else None
        return repos
    except Exception as e:
        logger.warning("github trending AI enrich failed: %s", e)
        for repo in repos:
            repo.setdefault("desc_zh", None)
            repo.setdefault("interpretation", None)
        return repos
    finally:
        session.close()


def _save_ai_batch(results: list):
    """Save a batch of AI interpretation results to DB. results = [{name, desc_zh, interpretation}]"""
    from core.database import GithubTrendingRepoAI
    session = _get_session()
    try:
        for item in results:
            name = item.get("name", "").strip()
            if not name:
                continue
            existing = session.query(GithubTrendingRepoAI).filter_by(repo_name=name).first()
            if existing:
                if item.get("desc_zh"):
                    existing.desc_zh = item["desc_zh"]
                if item.get("interpretation"):
                    existing.interpretation = item["interpretation"]
                existing.updated_at = datetime.utcnow()
            else:
                row = GithubTrendingRepoAI(
                    repo_name=name,
                    desc_zh=item.get("desc_zh", ""),
                    interpretation=item.get("interpretation", ""),
                )
                session.add(row)
        session.commit()
    except Exception as e:
        logger.warning("github trending AI save failed: %s", e)
        session.rollback()
    finally:
        session.close()


# ── Public action (called by TaskScheduler) ──

def fetch_and_cache_all_periods():
    """Fetch trending for all three periods and cache. Called by scheduled task."""
    for period in ("daily", "weekly", "monthly"):
        repos = _fetch_trending(period=period)
        if repos:
            _save_to_cache(period, repos)
            logger.info("github trending: cached %d repos for %s/%s", len(repos), _today_str(), period)


# ── Routes ──

def setup_github_trending_routes() -> APIRouter:
    router = APIRouter(prefix="/api/github-trending", tags=["github-trending"])

    @router.get("/list")
    def github_trending_list(
        period: str = Query("daily"),
        date: str = Query(None, alias="date"),
        force: int = Query(0),
    ):
        """Return GitHub trending repos with AI data. Uses DB cache unless force=1."""
        if period not in ("daily", "weekly", "monthly"):
            period = "daily"

        # If requesting a specific historical date, only use cache
        if date and date != _today_str():
            cached = _load_from_cache(period, snapshot_date=date)
            if cached is not None:
                return {"repos": _enrich_with_ai(cached), "date": date, "period": period, "cached": True}
            return {"repos": [], "date": date, "period": period, "cached": False}

        # Today: check cache first (unless force)
        if not force:
            cached = _load_from_cache(period)
            if cached is not None:
                return {"repos": _enrich_with_ai(cached), "date": _today_str(), "period": period, "cached": True}

        # Fetch from GitHub
        repos = _fetch_trending(period=period)
        if repos:
            _save_to_cache(period, repos)
        return {"repos": _enrich_with_ai(repos), "date": _today_str(), "period": period, "cached": False}

    @router.get("/history")
    def github_trending_history():
        """Return available cached dates."""
        from core.database import GithubTrending
        session = _get_session()
        try:
            rows = session.query(GithubTrending.date, GithubTrending.period)\
                .order_by(GithubTrending.date.desc())\
                .limit(90).all()
            dates = {}
            for row in rows:
                if row.date not in dates:
                    dates[row.date] = []
                if row.period not in dates[row.date]:
                    dates[row.date].append(row.period)
            return {"dates": [
                {"date": d, "periods": sorted(ps)} for d, ps in sorted(dates.items(), reverse=True)
            ]}
        except Exception as e:
            logger.warning("github trending history query failed: %s", e)
            return {"dates": []}
        finally:
            session.close()

    @router.post("/interpret")
    async def github_trending_interpret(
        period: str = Query("daily"),
        date: str = Query(None, alias="date"),
    ):
        """Use LLM to generate Chinese translation + AI interpretation for repos.
        Caches per-repo (cross-period reuse). Only calls LLM for uncached repos."""
        if period not in ("daily", "weekly", "monthly"):
            period = "daily"

        d = date or _today_str()
        repos = _load_from_cache(period, snapshot_date=d)
        if repos is None:
            return JSONResponse({"error": "No cached data for this date/period"}, status_code=404)

        # Find which repos already have AI data
        from core.database import GithubTrendingRepoAI
        session = _get_session()
        try:
            names = [r["name"] for r in repos]
            existing_ai = session.query(GithubTrendingRepoAI.repo_name)\
                .filter(GithubTrendingRepoAI.repo_name.in_(names)).all()
            cached_names = {row[0] for row in existing_ai}
        finally:
            session.close()

        # Filter to only repos needing AI
        uncached = [r for r in repos if r["name"] not in cached_names]
        if not uncached:
            # All already cached — just return enriched data
            return {"repos": _enrich_with_ai(repos), "date": d, "period": period, "ai_cached": True}

        # Resolve LLM endpoint
        from src.endpoint_resolver import resolve_endpoint
        url, model, headers = resolve_endpoint("utility")
        if not url or not model:
            url, model, headers = resolve_endpoint("default")
        if not url or not model:
            return JSONResponse({"error": "No AI model configured. Set a utility or default model in settings."}, status_code=400)

        # Build prompt
        repo_lines = []
        for i, repo in enumerate(uncached, 1):
            repo_lines.append(f'{i}. {repo["name"]} - {repo.get("desc", "No description")}')

        system_prompt = (
            "你是 GitHub 热榜分析师。对以下项目逐一提供：\n"
            "1. desc_zh: 项目描述的中文翻译（保留技术术语如 API、SDK、LLM 等原文）\n"
            "2. interpretation: 一句话中文解读这个项目为什么值得关注、解决什么问题\n"
            "严格按 JSON 数组格式返回，不要加 markdown 代码块或额外文字。\n"
            '格式: [{"name":"owner/repo","desc_zh":"中文翻译","interpretation":"AI解读"}]'
        )
        user_prompt = "\n".join(repo_lines)

        # Call LLM
        from src.llm_core import llm_call_async
        try:
            raw = await llm_call_async(
                url=url, model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3, max_tokens=4096,
                headers=headers, timeout=120,
            )
        except Exception as e:
            logger.warning("github trending AI interpret failed: %s", e)
            return JSONResponse({"error": f"LLM call failed: {e}"}, status_code=500)

        # Parse LLM response
        try:
            # Strip markdown code fences if present
            clean = raw.strip()
            if clean.startswith("```"):
                clean = re.sub(r'^```\w*\n?', '', clean)
                clean = re.sub(r'\n?```$', '', clean)
            ai_results = json.loads(clean)
            if not isinstance(ai_results, list):
                raise ValueError("Expected JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("github trending AI parse failed: %s, raw: %s", e, raw[:200])
            return JSONResponse({"error": f"Failed to parse AI response: {e}"}, status_code=500)

        # Save to DB
        _save_ai_batch(ai_results)

        # Return enriched data
        return {"repos": _enrich_with_ai(repos), "date": d, "period": period, "ai_cached": False, "interpreted": len(ai_results)}

    return router


def register_github_trending_page(app):
    """Register GET /github-trending to serve the HTML page."""
    @app.get("/github-trending", response_class=HTMLResponse)
    def serve_github_trending_page(request: Request):
        if not _HTML_PATH.exists():
            return HTMLResponse(
                "<h1>Page not found</h1><p>static/github-trending.html is missing.</p>",
                status_code=404,
            )
        with open(_HTML_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        nonce = getattr(request.state, "csp_nonce", "") if hasattr(request.state, "csp_nonce") else ""
        if nonce:
            html = html.replace('nonce="{{CSP_NONCE}}"', f'nonce="{nonce}"')
            html = html.replace('{{CSP_NONCE}}', nonce)
        return HTMLResponse(html)
