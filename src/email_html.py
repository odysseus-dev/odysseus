"""Rendering the compose body into the email's HTML part.

Lives on its own so the two send paths — the web composer in
``routes/email_routes.py`` and the agent's mail tool in
``mcp_servers/email_server.py`` — share one renderer. Importing the routes
module from the MCP server to reach it pulls the whole web app in and
trips a circular import, and writing a second renderer would mean two
formatting paths drifting apart.
"""

from __future__ import annotations

import html
import re


def md_to_email_html(text: str) -> str:
    """Render the compose markdown body to a SAFE HTML fragment for the email's
    text/html part. Everything is HTML-escaped FIRST (so a pasted <script> /
    <img onerror=...> can never become live HTML in the recipient's client),
    then the toolbar's formatting is layered on with controlled regex: bold,
    italic, strike, inline code, http(s) links, headings, and bullet/numbered
    lists. Plain-text readers still get the raw markdown via the text/plain part.
    """
    def _inline(s: str) -> str:
        s = html.escape(s)                                  # escape BEFORE formatting
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
        s = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        # links: text + http(s) url only (escape() already neutralised quotes)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        return s

    parts: list[str] = []
    in_ul = in_ol = False
    for ln in (text or "").split("\n"):
        m_h = re.match(r"^(#{1,3})\s+(.*)$", ln)
        m_ul = re.match(r"^\s*[-*]\s+(.*)$", ln)
        m_ol = re.match(r"^\s*\d+\.\s+(.*)$", ln)
        if m_h:
            if in_ul: parts.append("</ul>"); in_ul = False
            if in_ol: parts.append("</ol>"); in_ol = False
            lvl = len(m_h.group(1))
            parts.append(f"<h{lvl}>{_inline(m_h.group(2))}</h{lvl}>")
        elif m_ul:
            if in_ol: parts.append("</ol>"); in_ol = False
            if not in_ul: parts.append("<ul>"); in_ul = True
            parts.append(f"<li>{_inline(m_ul.group(1))}</li>")
        elif m_ol:
            if in_ul: parts.append("</ul>"); in_ul = False
            if not in_ol: parts.append("<ol>"); in_ol = True
            parts.append(f"<li>{_inline(m_ol.group(1))}</li>")
        else:
            if in_ul: parts.append("</ul>"); in_ul = False
            if in_ol: parts.append("</ol>"); in_ol = False
            parts.append(_inline(ln) + "<br>")
    if in_ul: parts.append("</ul>")
    if in_ol: parts.append("</ol>")
    return "<html><body>" + "\n".join(parts) + "</body></html>"
