"""Model Hub SPA injection and /model-hub route."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("titan.hub-ui")

_SCRIPT_TAG = (
    '<script src="/static/js/titanSchedulerStatus.js?v=20260708c"></script>\n'
    '<script src="/static/js/schedulerPanel.js?v=20260708c"></script>\n'
    '<script src="/static/js/titanImageGen.js?v=20260702d"></script>\n'
    '<script src="/static/js/titanImageProposal.js?v=20260702c"></script>\n'
    '<script src="/static/js/titanImageActions.js?v=20260702a"></script>\n'
    '<script src="/static/js/titanLlmGuard.js?v=20260701d"></script>\n'
    '<script src="/static/js/modelHub.js?v=20260708c"></script>\n'
    '<script src="/static/js/cookbook-deprecate.js?v=20260626c"></script>'
)


def _hub_bootstrap(nonce: str) -> str:
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    return (
        f'<script id="titan-hub-bootstrap"{nonce_attr}>'
        '(function(){if(window.__titanHubBoot)return;window.__titanHubBoot=1;'
        "if('serviceWorker' in navigator){navigator.serviceWorker.getRegistration()"
        ".then(function(r){if(r)r.update();});}"
        "function h(){if(window.titanModelHub&&window.titanModelHub.open)"
        "window.titanModelHub.open();else window.__titanOpenHubPending=1;}"
        'document.addEventListener("click",function(e){var t=e.target&&e.target.closest&&'
        'e.target.closest("#tool-cookbook-btn,#rail-cookbook");if(!t)return;'
        "e.preventDefault();e.stopImmediatePropagation();h();},true);"
        'function r(){var a=document.getElementById("rail-cookbook");'
        'if(a)a.title="Model Hub";var g=document.querySelector("#tool-cookbook-btn .grow");'
        'if(g)g.textContent="Model Hub";'
        'var v=document.querySelector(\'[data-ui-key="tool-cookbook"]\');'
        'if(v){var l=v.closest(".vis-row");if(l){var s=l.querySelector(".vis-label");if(s)s.textContent="Model Hub";}}'
        'if(location.pathname==="/cookbook"||location.pathname==="/model-hub")document.title="Model Hub — TITAN";}'
        'if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",r);'
        "else r();})();</script>"
    )


def inject_titan_hub(html: str, nonce: str) -> str:
    if "tool-cookbook-btn" not in html and "rail-cookbook" not in html:
        return html
    if "titan-hub-bootstrap" not in html and "<head>" in html:
        html = html.replace("<head>", "<head>\n" + _hub_bootstrap(nonce), 1)
    if "modelHub.js" not in html and "</body>" in html:
        html = html.replace("</body>", _SCRIPT_TAG + "\n</body>", 1)
    return html


def register_hub_ui(app: "FastAPI") -> None:
    import app as app_module

    _orig = app_module._serve_html_with_nonce

    def _titan_serve_html_with_nonce(request: Request, file_path: str):
        response = _orig(request, file_path)
        try:
            body = response.body.decode("utf-8")
        except Exception:
            return response
        if "tool-cookbook-btn" not in body and "rail-cookbook" not in body:
            return response
        nonce = getattr(request.state, "csp_nonce", "")
        injected = inject_titan_hub(body, nonce)
        if injected == body:
            return response
        return HTMLResponse(injected, status_code=response.status_code)

    app_module._serve_html_with_nonce = _titan_serve_html_with_nonce

    @app.get("/model-hub")
    async def serve_model_hub(request: Request):
        return await app_module.serve_index(request)
