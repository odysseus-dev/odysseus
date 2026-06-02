#!/usr/bin/env python
"""Minimal, dependency-free GNU gettext PO reader/writer.

Implements the subset Odysseus needs — header, ``msgctxt``, ``msgid``,
``msgstr``, flags (``#,``), extracted (``#.``) and translator (``#``) comments,
and multi-line strings — with no third-party packages (stdlib only) so it runs
the same on Windows and Linux without a system gettext install.

Round-trips losslessly at the string level: ``parse(dump(entries))`` yields the
same ``msgctxt``/``msgid``/``msgstr`` values back. Plural entries
(``msgid_plural`` / ``msgstr[N]``) are parsed and preserved but Odysseus's flat
source-keyed catalogs do not use them yet.

This module is intentionally small and self-contained. When the system gettext
tools (``msgmerge``, ``msginit``, ``msgfmt``) are present they can operate on the
files this writes; when they are not, the sibling scripts fall back to the pure
helpers here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# --- string (un)escaping -------------------------------------------------

_ESCAPE = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_UNESCAPE = {
    "n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
    "a": "\a", "b": "\b", "f": "\f", "v": "\v",
}


def escape(s: str) -> str:
    return "".join(_ESCAPE.get(ch, ch) for ch in s)


def unescape(s: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(_UNESCAPE.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _fmt(keyword: str, s: str) -> str:
    """Render ``keyword "value"``; split multi-line values gettext-style."""
    if "\n" not in s:
        return f'{keyword} "{escape(s)}"'
    lines = s.split("\n")
    segs = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    if segs and segs[-1] == "":  # value ended in a newline; drop empty tail chunk
        segs.pop()
    body = "\n".join(f'"{escape(seg)}"' for seg in segs)
    return f'{keyword} ""\n{body}'


# --- model ---------------------------------------------------------------

@dataclass
class Entry:
    msgid: str = ""
    msgstr: str = ""
    msgctxt: Optional[str] = None
    flags: List[str] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)      # "#. " extracted
    tcomments: List[str] = field(default_factory=list)     # "# " translator
    # plural support (preserved, unused by the flat catalogs)
    msgid_plural: Optional[str] = None
    msgstr_plural: List[str] = field(default_factory=list)

    @property
    def key(self):
        return (self.msgctxt, self.msgid)

    @property
    def is_header(self) -> bool:
        # The PO header is the empty-msgid entry whose value holds the header
        # fields; a real (rare) empty source string would not contain these.
        return (self.msgid == "" and self.msgctxt is None
                and "MIME-Version" in self.msgstr)

    def dump(self) -> str:
        out: List[str] = []
        out += [f"# {c}".rstrip() for c in self.tcomments]
        out += [f"#. {c}".rstrip() for c in self.comments]
        if self.flags:
            out.append("#, " + ", ".join(self.flags))
        if self.msgctxt is not None:
            out.append(_fmt("msgctxt", self.msgctxt))
        out.append(_fmt("msgid", self.msgid))
        if self.msgid_plural is not None:
            out.append(_fmt("msgid_plural", self.msgid_plural))
            for i, s in enumerate(self.msgstr_plural):
                out.append(_fmt(f"msgstr[{i}]", s))
        else:
            out.append(_fmt("msgstr", self.msgstr))
        return "\n".join(out)


# --- parsing -------------------------------------------------------------

def _quoted(line: str) -> str:
    """Return the unescaped content of a ``"..."`` PO line."""
    a = line.find('"')
    b = line.rfind('"')
    if a < 0 or b <= a:
        return ""
    return unescape(line[a + 1:b])


def parse(text: str) -> List[Entry]:
    entries: List[Entry] = []
    cur = Entry()
    last = None          # which field is accepting continuation strings
    plural_idx = None
    have = False         # current entry has seen content

    def flush():
        nonlocal cur, last, plural_idx, have
        if have:
            entries.append(cur)
        cur = Entry()
        last = None
        plural_idx = None
        have = False

    # Split on "\n" only (not str.splitlines(), which also breaks on U+2028,
    # U+2029, FF, VT, NEL — none of which gettext escapes — and would silently
    # truncate any value containing them). rstrip("\r") tolerates CRLF files.
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.strip() == "":
            flush()
            continue
        if line.startswith("#"):
            if line.startswith("#,"):
                cur.flags += [f.strip() for f in line[2:].split(",") if f.strip()]
            elif line.startswith("#."):
                cur.comments.append(line[2:].strip())
            elif line.startswith("#:") or line.startswith("#|") or line.startswith("#~"):
                pass  # references / previous / obsolete — ignored on read
            else:
                cur.tcomments.append(line[1:].strip())
            have = True
            continue
        if line.startswith("msgctxt"):
            cur.msgctxt = _quoted(line); last = "msgctxt"; have = True
        elif line.startswith("msgid_plural"):
            cur.msgid_plural = _quoted(line); last = "msgid_plural"; have = True
        elif line.startswith("msgid"):
            cur.msgid = _quoted(line); last = "msgid"; have = True
        elif line.startswith("msgstr["):
            idx = int(line[line.find("[") + 1:line.find("]")])
            while len(cur.msgstr_plural) <= idx:
                cur.msgstr_plural.append("")
            cur.msgstr_plural[idx] = _quoted(line); last = "plural"; plural_idx = idx; have = True
        elif line.startswith("msgstr"):
            cur.msgstr = _quoted(line); last = "msgstr"; have = True
        elif line.startswith('"'):
            seg = _quoted(line)
            if last == "msgctxt":
                cur.msgctxt = (cur.msgctxt or "") + seg
            elif last == "msgid":
                cur.msgid += seg
            elif last == "msgid_plural":
                cur.msgid_plural = (cur.msgid_plural or "") + seg
            elif last == "plural" and plural_idx is not None:
                cur.msgstr_plural[plural_idx] += seg
            elif last == "msgstr":
                cur.msgstr += seg
    flush()
    return entries


def dump(entries: List[Entry]) -> str:
    return "\n\n".join(e.dump() for e in entries) + "\n"


# --- header helpers ------------------------------------------------------

def make_header(fields: "dict[str, str]", tcomment: str = "") -> Entry:
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())
    e = Entry(msgid="", msgstr=body)
    if tcomment:
        e.tcomments = tcomment.split("\n")
    e.flags = ["fuzzy"]  # standard for a generated header until reviewed
    return e


def parse_header(entry: Entry) -> "dict[str, str]":
    out: "dict[str, str]" = {}
    for line in entry.msgstr.split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out
