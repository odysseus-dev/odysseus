"""VRAM Scheduler panel SPA injection and /scheduler route."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("titan.scheduler-ui")

_SCRIPT_TAG = (
    '<script src="/static/js/titanSchedulerStatus.js?v=20260708b"></script>\n'
    '<script src="/static/js/schedulerPanel.js?v=20260708b"></script>'
)

_SCHEDULER_ICON = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="4" y="4" width="16" height="16" rx="2"/>'
    '<rect x="9" y="9" width="6" height="6"/>'
    '<line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/>'
    '<line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/>'
    '<line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="14" x2="22" y2="14"/>'
    '<line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="14" x2="4" y2="14"/>'
    "</svg>"
)


def _scheduler_bootstrap(nonce: str) -> str:
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    return (
        f'<script id="titan-scheduler-bootstrap"{nonce_attr}>'
        '(function(){if(window.__titanSchedulerBoot)return;window.__titanSchedulerBoot=1;'
        "function o(){if(window.titanSchedulerPanel&&window.titanSchedulerPanel.open)"
        "window.titanSchedulerPanel.open();else window.__titanOpenSchedulerPending=1;}"
        'document.addEventListener("click",function(e){var t=e.target&&e.target.closest&&'
        'e.target.closest("#tool-scheduler-btn,#rail-scheduler");if(!t)return;'
        "e.preventDefault();e.stopImmediatePropagation();o();},true);"
        'if(location.pathname==="/scheduler")document.title="VRAM Scheduler — TITAN";'
        "})();</script>"
    )


def inject_titan_scheduler(html: str, nonce: str) -> str:
    if "rail-tools-scroll" not in html:
        return html
    if "titan-scheduler-bootstrap" not in html and "<head>" in html:
        html = html.replace("<head>", "<head>\n" + _scheduler_bootstrap(nonce), 1)
    if 'id="rail-scheduler"' not in html and 'id="rail-cookbook"' in html:
        html = html.replace(
            'id="rail-cookbook"',
            'id="rail-scheduler" title="VRAM Scheduler">' + _SCHEDULER_ICON + '</button>\n'
            '    <button class="icon-rail-btn" id="rail-cookbook"',
            1,
        )
    if "schedulerPanel.js" not in html and "</body>" in html:
        html = html.replace("</body>", _SCRIPT_TAG + "\n</body>", 1)
    return html


def register_scheduler_ui(app: "FastAPI") -> None:
    import app as app_module

    _orig = app_module._serve_html_with_nonce

    def _titan_serve_html_with_nonce(request: Request, file_path: str):
        response = _orig(request, file_path)
        try:
            body = response.body.decode("utf-8")
        except Exception:
            return response
        if "rail-tools-scroll" not in body:
            return response
        nonce = getattr(request.state, "csp_nonce", "")
        injected = inject_titan_scheduler(body, nonce)
        if injected == body:
            return response
        return HTMLResponse(injected, status_code=response.status_code)

    app_module._serve_html_with_nonce = _titan_serve_html_with_nonce

    @app.get("/scheduler")
    async def serve_scheduler(request: Request):
        return await app_module.serve_index(request)
