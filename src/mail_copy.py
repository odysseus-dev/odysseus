"""Email copies of Odysseus output (chat replies, Deep Research reports).

Timeweb blocks outbound SMTP, so mail goes through the whisper_bot HTTPS relay
on Hetzner (POST /mail-relay with a shared secret). Every send runs in a daemon
thread: a slow or failing relay never delays or breaks a chat turn.

Env:
  MAIL_RELAY_URL, MAIL_RELAY_SECRET — the relay (required for any mail)
  RESEARCH_EMAIL_TO — recipient for Deep Research reports
  CHAT_EMAIL_TO     — recipient for every web-chat assistant reply
"""

import html as _html
import logging
import os
import threading

logger = logging.getLogger(__name__)


def _markdown_to_html(text: str) -> str:
    # Model output (built from fetched web pages) may carry raw HTML; Python-Markdown
    # passes raw HTML through verbatim, so neutralise tags first. Only "<" is escaped:
    # ">" blockquotes and "&" entities keep rendering as Markdown.
    safe = (text or "").replace("<", "&lt;")
    try:
        import markdown as _md
        return _md.markdown(safe, extensions=["tables", "fenced_code"])
    except Exception:
        return f"<pre>{_html.escape(text or '')}</pre>"


def _sources_html(sources) -> str:
    links = "".join(
        f'<li><a href="{_html.escape(s.get("url", ""))}">{_html.escape(s.get("title") or s.get("url", ""))}</a></li>'
        for s in (sources or [])[:60] if isinstance(s, dict) and s.get("url")
    )
    return f"<h2>Источники</h2><ol>{links}</ol>" if links else ""


def send_copy(recipient_env: str, subject: str, markdown_body: str, *,
              sources=None, preface_html: str = "", tag: str = "") -> bool:
    """Queue an email copy. Returns False (and sends nothing) when not configured."""
    to = (os.environ.get(recipient_env) or "").strip()
    url = (os.environ.get("MAIL_RELAY_URL") or "").strip()
    secret = (os.environ.get("MAIL_RELAY_SECRET") or "").strip()
    if not (to and url and secret and (markdown_body or "").strip()):
        return False
    body = preface_html + _markdown_to_html(markdown_body) + _sources_html(sources)
    payload = {"to": to, "subject": " ".join((subject or "Odysseus").split())[:150], "body": body}

    def _send():
        import httpx
        try:
            r = httpx.post(url, json=payload, headers={"x-relay-secret": secret}, timeout=60)
            if r.status_code == 200:
                logger.info(f"Mail copy sent ({tag}, {len(markdown_body)} chars)")
            else:
                logger.error(f"Mail copy relay returned HTTP {r.status_code} ({tag})")
        except Exception as e:
            logger.error(f"Mail copy failed ({tag}): {e}")

    threading.Thread(target=_send, name=f"mail-copy-{tag}", daemon=True).start()
    return True


def send_telegram_copy(text: str, *, tag: str = "") -> bool:
    """Queue a Telegram copy via the whisper_bot relay (POST /tg-relay).

    The relay holds the bot token and a fixed recipient; enabled by TG_RELAY_URL
    (+ the shared MAIL_RELAY_SECRET). Long texts are split by the relay.
    """
    url = (os.environ.get("TG_RELAY_URL") or "").strip()
    secret = (os.environ.get("MAIL_RELAY_SECRET") or "").strip()
    if not (url and secret and (text or "").strip()):
        return False

    def _send():
        import httpx
        try:
            # The relay paces parts (~1 s each) and waits out Telegram 429s.
            r = httpx.post(url, json={"text": text}, headers={"x-relay-secret": secret}, timeout=300)
            if r.status_code == 200:
                logger.info(f"Telegram copy sent ({tag}, {len(text)} chars)")
            else:
                logger.error(f"Telegram copy relay returned HTTP {r.status_code} ({tag})")
        except Exception as e:
            logger.error(f"Telegram copy failed ({tag}): {e}")

    threading.Thread(target=_send, name=f"tg-copy-{tag}", daemon=True).start()
    return True


def _question_and_title(sess):
    history = getattr(sess, "history", None) or []
    question = next((m.content for m in reversed(history)
                     if getattr(m, "role", "") == "user" and (m.content or "").strip()), "")
    title = (getattr(sess, "name", "") or "").strip() or " ".join(question.split())[:80] or "чат"
    return question.strip(), title


def email_chat_reply(sess, session_id: str, reply: str) -> bool:
    """Copy a web-chat assistant reply (with the question it answers) to email and Telegram."""
    question, title = _question_and_title(sess)
    preface = ""
    if question:
        preface = ("<p><b>Вопрос:</b></p><blockquote>"
                   + _html.escape(question[:4000]).replace("\n", "<br>")
                   + "</blockquote><p><b>Ответ Odysseus:</b></p>")
    tag = f"chat-{session_id[:8]}"
    emailed = send_copy("CHAT_EMAIL_TO", f"Odysseus: {title}", reply,
                        preface_html=preface, tag=tag)
    tg_text = (f"💬 Odysseus: {title}\n\n❓ {question[:500]}\n\n" if question else f"💬 Odysseus: {title}\n\n") + reply
    sent_tg = send_telegram_copy(tg_text, tag=tag)
    return emailed or sent_tg
