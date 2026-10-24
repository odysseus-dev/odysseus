"""The outgoing signature block appended to mail sent from an account.

One account, one signature. It is stored as plain text on
``EmailAccount.signature`` and travels through the same body path as
everything the user types, so the markdown renderer that builds the HTML
part renders it too — there is no second formatting path to keep in step.

The delimiter
-------------
RFC 3676 §4.3 defines the signature separator as a line containing exactly
``"-- "`` — two hyphens, a space, nothing else. Receiving clients look for
it to fold the signature away, to keep it out of the quoted text when
someone replies, and to leave it out of a thread summary. Getting the
trailing space right is the whole point: ``"--"`` is just a line of
hyphens, and the signature stops being a signature to every client that
follows the spec.

Placement
---------
In a reply the signature belongs after what the user wrote and *before* the
quoted original — that is where every mail client puts it, and a signature
below a long quote is a signature nobody reads. `apply_signature` finds the
quote boundary and inserts above it, falling back to appending when there
is nothing quoted.

The image
---------
An account can also carry a logo or a scanned sign-off. It travels as an
inline MIME part referenced by Content-ID, not as a remote URL, because
Outlook and Gmail block remote images by default and a hosted logo would
reach most recipients as an empty box. It renders only in the HTML part —
there is no way to show a picture in text/plain — so the text signature
stays the thing that always arrives.

The image rides on the text. `apply_signature` puts the text in the draft
where the user can see it, so deleting it there is how you send an
unsigned message; if the image were attached regardless, the logo would
still go out on a message the user deliberately unsigned. The send path
therefore attaches it only when the text survived.

Applying twice
--------------
The composer inserts the signature into the draft so the user can see and
edit it before sending, which means the body reaching the send route
usually already has one. Anything that appends server-side has to notice
that, or a reply ends with the sender's name and phone number twice.
`body_has_signature` is that check, and `apply_signature` makes it for you.
"""

from __future__ import annotations

import base64
import binascii
import re

# RFC 3676 §4.3. The trailing space is load-bearing; see the module docstring.
SIGNATURE_DELIMITER = "-- "

# A signature is a few lines of contact details. The cap exists so a paste
# accident cannot put a novel on the end of every message the account sends.
MAX_SIGNATURE_CHARS = 4000

# Where the quoted original starts in a draft this app built. Both markers
# are emitted by the composer when it builds a reply or a forward.
_QUOTE_MARKER_RE = re.compile(
    r"^-{3,}\s*(?:Previous|Forwarded|Original)\s+[Mm]essage\s*-{3,}\s*$"
)

# Attribution line above a quote, for bodies that came from somewhere else
# ("On Tue, 3 Jun 2026 at 09:14, Ada <ada@example.com> wrote:").
_ATTRIBUTION_RE = re.compile(r"^.*\b(?:wrote|escreveu|schrieb|a écrit)\s*:\s*$")


def normalize_signature(raw) -> str:
    """Clean a signature as typed into the settings field.

    Trailing whitespace goes (it survives a copy-paste and shows up as
    ragged lines in some clients), runs of blank lines collapse, and a
    delimiter the user typed themselves is removed — the send path adds
    exactly one, and two in a row means the second is quoted as content.
    """
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]

    # Drop a leading delimiter, whichever way it was written.
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip() in {"--", "-- "}:
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()

    cleaned: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
        else:
            blanks += 1
            if blanks > 1:
                continue
        cleaned.append(line)
    return "\n".join(cleaned)[:MAX_SIGNATURE_CHARS]


def signature_block(signature) -> str:
    """The delimiter plus the signature, or "" when there is nothing to add."""
    cleaned = normalize_signature(signature)
    if not cleaned:
        return ""
    return f"{SIGNATURE_DELIMITER}\n{cleaned}"


def account_signature(cfg) -> str:
    """The signature to use for a resolved send config, honouring the toggle."""
    cfg = cfg or {}
    if not cfg.get("signature_enabled", True):
        return ""
    return normalize_signature(cfg.get("signature"))


def body_has_signature(body, signature) -> bool:
    """Whether *body* already ends with this signature.

    Compared on stripped lines so a draft the user lightly reflowed still
    counts as signed — the question being answered is "would appending
    duplicate it", not "is it byte-identical".
    """
    cleaned = normalize_signature(signature)
    if not cleaned:
        return False

    def _key(text: str) -> list:
        return [line.strip() for line in text.split("\n") if line.strip()]

    needle = _key(cleaned)
    haystack = _key(str(body or ""))
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i:i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def _quote_start(lines: list) -> int | None:
    """Index of the first line of the quoted original, or None."""
    for idx, line in enumerate(lines):
        if _QUOTE_MARKER_RE.match(line):
            return idx
        if line.startswith(">"):
            # Step back over the attribution line and the blank line above it,
            # so the signature does not land between "…wrote:" and the quote.
            start = idx
            if start and _ATTRIBUTION_RE.match(lines[start - 1]):
                start -= 1
            return start
    return None


def apply_signature(body, signature) -> str:
    """Return *body* with the signature block in place.

    A body that already carries the signature is returned untouched, so this
    is safe to call on a draft the composer already signed.
    """
    block = signature_block(signature)
    if not block:
        return str(body or "")
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    if body_has_signature(text, signature):
        return text

    lines = text.split("\n")
    cut = _quote_start(lines)
    if cut is None:
        return f"{text.rstrip()}\n\n{block}\n" if text.strip() else f"{block}\n"

    above = "\n".join(lines[:cut]).rstrip()
    below = "\n".join(lines[cut:])
    lead = f"{above}\n\n" if above else ""
    return f"{lead}{block}\n\n{below}"


# ── The signature image ──────────────────────────────────────────────────
#
# Magic bytes, not the declared media type: a browser will happily label a
# .exe as image/png in a multipart upload, and the type is what ends up in
# the MIME header telling the recipient's client how to decode the part.
# Sniffing means the header describes the bytes actually sent.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

SIGNATURE_IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif")

# A logo, not a photograph. Every message the account sends carries this,
# so the cap is about the recipient's mailbox quota as much as ours: at
# 256 KB a hundred messages cost 25 MB of someone else's storage. Base64
# inflates by a third on the wire, which the cap is chosen to absorb.
MAX_SIGNATURE_IMAGE_BYTES = 256 * 1024

# Content-ID for the inline part. Fixed rather than random because it is
# scoped to one message: each outgoing mail carries at most one signature
# image, and a stable value keeps the HTML snippet a constant.
SIGNATURE_IMAGE_CID = "odysseus-signature-image"

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>image/[a-z0-9.+-]+)\s*;\s*base64\s*,", re.IGNORECASE
)


class SignatureImageError(ValueError):
    """The uploaded bytes are not an image this can send."""


def _sniff_image_type(payload: bytes):
    for magic, mime in _IMAGE_MAGIC:
        if payload.startswith(magic):
            return mime
    return None


def normalize_signature_image(raw):
    """Validate an uploaded signature image.

    Accepts a ``data:`` URL or bare base64 and returns ``(b64, mime)`` with
    the media type read from the bytes themselves, or ``(None, None)`` when
    there is nothing to store. Raises `SignatureImageError` for anything
    that is not a PNG, JPEG or GIF within the size cap.
    """
    text = str(raw or "").strip()
    if not text:
        return None, None

    m = _DATA_URL_RE.match(text)
    if m:
        text = text[m.end():]
    # Whitespace survives a copy-pasted data URL and breaks strict decoding.
    text = re.sub(r"\s+", "", text)
    if not text:
        return None, None

    try:
        payload = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureImageError("Signature image must be base64-encoded image bytes")
    if not payload:
        return None, None
    if len(payload) > MAX_SIGNATURE_IMAGE_BYTES:
        kb = MAX_SIGNATURE_IMAGE_BYTES // 1024
        raise SignatureImageError(f"Signature image must be {kb} KB or smaller")

    mime = _sniff_image_type(payload)
    if mime is None:
        raise SignatureImageError("Signature image must be a PNG, JPEG or GIF")

    # Re-encode rather than passing the input through: this drops the
    # padding and line-break variations different uploaders produce, so
    # what lands in the database is one canonical form.
    return base64.b64encode(payload).decode("ascii"), mime


def account_signature_image(cfg):
    """The image for a resolved send config, honouring the toggle.

    Returns ``(b64, mime)``, or ``(None, None)`` when the account has no
    image or the signature is switched off — the switch governs the whole
    block, image included.
    """
    cfg = cfg or {}
    if not cfg.get("signature_enabled", True):
        return None, None
    data = (cfg.get("signature_image") or "").strip()
    if not data:
        return None, None

    # Decode here, always. The caller is a send path, and an image that
    # cannot be decoded would otherwise raise while the message is being
    # assembled — losing the whole mail over a decoration. A column holding
    # something unreadable makes the account send as if it had no image.
    try:
        payload = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None, None
    if not payload:
        return None, None

    # The bytes decide the media type, not the stored column: a row written
    # before the type was recorded, or edited by hand, still goes out with a
    # header that matches its content.
    mime = _sniff_image_type(payload)
    if mime is None:
        return None, None
    return data, mime


def signature_image_html(cid: str = SIGNATURE_IMAGE_CID) -> str:
    """The <img> that references the inline part.

    `alt` is empty on purpose: a logo beside a name the reader already has
    in text adds nothing to a screen reader, and "company logo" read aloud
    on every message is noise. The inline style keeps a large upload from
    stretching the message column in clients that ignore width attributes.
    """
    return (
        f'<div style="margin-top:8px"><img src="cid:{cid}" alt="" '
        f'style="max-width:320px;height:auto;border:0;display:block"></div>'
    )


def html_with_signature_image(html_part: str, cid: str = SIGNATURE_IMAGE_CID) -> str:
    """Put the image at the end of the rendered HTML body.

    The renderers escape everything they are given, so the image cannot
    come from the body text — it is appended structurally here, which is
    also what keeps a pasted `<img>` in a draft from becoming live HTML.
    """
    text = str(html_part or "")
    img = signature_image_html(cid)
    lower = text.lower()
    for closer in ("</body>", "</html>"):
        idx = lower.rfind(closer)
        if idx != -1:
            return text[:idx] + img + text[idx:]
    return text + img


def signature_image_part(b64: str, mime: str, cid: str = SIGNATURE_IMAGE_CID):
    """The inline MIME part the HTML references by Content-ID.

    `inline` disposition and a Content-ID are what separate "render this
    where the body points at it" from "offer this as a download"; a part
    missing either shows up in the recipient's attachment list.
    """
    from email.mime.image import MIMEImage

    payload = base64.b64decode(b64, validate=True)
    subtype = (mime or "image/png").split("/", 1)[-1] or "png"
    part = MIMEImage(payload, _subtype=subtype)
    # The angle brackets are required by RFC 2392 — `cid:` URLs in the HTML
    # reference the value inside them.
    part.add_header("Content-ID", f"<{cid}>")
    part.add_header("Content-Disposition", "inline", filename="signature")
    return part
