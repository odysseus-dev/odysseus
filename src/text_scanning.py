"""Forward-only helpers for permissive text grammars.

These helpers retain the existing regular expressions as anchored token
parsers while preventing ``re.search`` from retrying the same token suffix at
every embedded prefix.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Match, Pattern


def iter_prefixed_token_matches(
    text: str,
    candidate_re: Pattern[str],
    anchored_re: Pattern[str],
    token_tail_re: Pattern[str],
) -> Iterator[Match[str]]:
    """Yield legacy greedy matches after testing one prefix per token.

    ``anchored_re`` must start with the same fixed prefix recognized by
    ``candidate_re``. ``token_tail_re`` describes characters that the
    anchored grammar can consume after that prefix. If the first prefix in
    such a token cannot match, a later embedded prefix cannot match either:
    its suffix was already available to the first attempt. Advancing to the
    token boundary makes failed scans linear without changing successful
    greedy captures.
    """
    pos = 0
    while candidate := candidate_re.search(text, pos):
        match = anchored_re.match(text, candidate.start())
        if match is not None:
            yield match
            pos = match.end()
            continue
        tail = token_tail_re.match(text, candidate.end())
        pos = max(candidate.end(), tail.end() if tail is not None else candidate.end())


def has_prefixed_token_match(
    text: str,
    candidate_re: Pattern[str],
    anchored_re: Pattern[str],
    token_tail_re: Pattern[str],
) -> bool:
    """Return whether ``iter_prefixed_token_matches`` yields a match."""
    return next(
        iter_prefixed_token_matches(text, candidate_re, anchored_re, token_tail_re),
        None,
    ) is not None


_HTTP_URL_PREFIX_RE = re.compile(r"https?://", re.IGNORECASE)
_VIDEO_DETAIL_BASE_RE = re.compile(
    r"\b(?:how\s+many|count|break\s*points?|timestamps?|what\s+time|score(?:board)?s?)\b",
    re.IGNORECASE,
)
_VIDEO_DETAIL_EXTENDED_RE = re.compile(
    r"\b(?:sequence|in\s+order|chronological|at\s+what\s+time)\b|"
    r"(?:多少|几次|何时|什么时候|时间|顺序)",
    re.IGNORECASE,
)
_WHEN_RE = re.compile(r"\bwhen\s+", re.IGNORECASE)
_WHEN_TARGET_RE = re.compile(r"(?:end|happen)\b", re.IGNORECASE)
_FIRST_RE = re.compile(r"\bfirst\s+", re.IGNORECASE)
_FIRST_TARGET_RE = re.compile(r"(?:save|attempt|event)\b", re.IGNORECASE)


def contains_search_engine_navigation(text: str) -> bool:
    """Match the legacy Google/Bing/DuckDuckGo navigation URL grammar.

    The old expression backtracked through every possible optional subdomain
    split. Parsing the host up to its first slash gives the same accepted
    hosts and path prefixes with one pass per URL candidate.
    """
    pos = 0
    while candidate := _HTTP_URL_PREFIX_RE.search(text, pos):
        host_start = candidate.end()
        path_start = text.find("/", host_start)
        if path_start < 0:
            return False
        host = text[host_start:path_start].casefold()
        path = text[path_start + 1:path_start + 7].casefold()
        google_at = host.rfind(".google.")
        recognized_host = (
            (host.startswith("google.") and len(host) > len("google."))
            or (google_at >= 0 and google_at + len(".google.") < len(host))
            or host == "bing.com"
            or host.endswith(".bing.com")
            or host == "duckduckgo.com"
            or host.endswith(".duckduckgo.com")
        )
        if recognized_host and path.startswith(("search", "sorry", "html", "lite", "?")):
            return True
        # A later URL may begin in the path. Resume after this scheme rather
        # than skipping the whole non-whitespace region.
        pos = candidate.end()
    return False


def contains_detailed_sequence_request(text: str, *, include_first: bool = True) -> bool:
    """Recognize count/order/timing requests without overlapping ``.*`` scans."""
    value = str(text or "")
    if _VIDEO_DETAIL_BASE_RE.search(value):
        return True
    if include_first and _VIDEO_DETAIL_EXTENDED_RE.search(value):
        return True
    pos = 0
    while when := _WHEN_RE.search(value, pos):
        line_end = value.find("\n", when.end())
        if line_end < 0:
            line_end = len(value)
        if _WHEN_TARGET_RE.search(value, when.end(), line_end) is not None:
            return True
        pos = line_end + 1
    if include_first:
        pos = 0
        while first := _FIRST_RE.search(value, pos):
            line_end = value.find("\n", first.end())
            if line_end < 0:
                line_end = len(value)
            if _FIRST_TARGET_RE.search(value, first.end(), line_end) is not None:
                return True
            pos = line_end + 1
    return False


def iter_angle_contents(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield nonempty flat ``<...>`` contents with monotonic delimiters."""
    pos = 0
    while (start := text.find("<", pos)) >= 0:
        end = text.find(">", start + 1)
        if end < 0:
            return
        if end > start + 1:
            yield start, end + 1, text[start + 1:end]
            pos = end + 1
        else:
            pos = start + 1


def iter_markdown_links(
    text: str,
    *,
    target_prefix: str = "",
    target_re: Pattern[str] | None = None,
) -> Iterator[tuple[int, int, str, str]]:
    """Yield flat Markdown links accepted by the legacy link regexes."""
    pos = 0
    marker = "](" + target_prefix
    while (start := text.find("[", pos)) >= 0:
        label_end = text.find("]", start + 1)
        if label_end < 0:
            return
        if label_end == start + 1:
            pos = start + 1
            continue
        if not text.startswith(marker, label_end):
            pos = label_end + 1
            continue
        target_start = label_end + len(marker)
        target_end = text.find(")", target_start)
        if target_end < 0:
            return
        target = text[target_start:target_end]
        if target and (target_re is None or target_re.fullmatch(target)):
            yield start, target_end + 1, text[start + 1:label_end], target
            pos = target_end + 1
        else:
            pos = label_end + 1


def replace_markdown_links_with_labels(text: str) -> str:
    """Linear equivalent of replacing flat Markdown links with their labels."""
    links = list(iter_markdown_links(text))
    if not links:
        return text
    out = []
    pos = 0
    for start, end, label, _target in links:
        out.extend((text[pos:start], label))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def first_tag_content(text: str, tag: str, *, allow_attributes: bool = False) -> str | None:
    """Return the first flat tag body using forward-only opener/closer scans."""
    opener_re = re.compile(r"<" + re.escape(tag), re.IGNORECASE)
    closer_re = re.compile(r"</" + re.escape(tag) + r">", re.IGNORECASE)
    pos = 0
    while opener := opener_re.search(text, pos):
        name_end = opener.end()
        if name_end < len(text) and text[name_end] == ">":
            body_start = name_end + 1
        elif allow_attributes and name_end < len(text) and text[name_end].isspace():
            tag_end = text.find(">", name_end + 1)
            if tag_end < 0:
                return None
            body_start = tag_end + 1
        else:
            pos = name_end
            continue
        closer = closer_re.search(text, body_start)
        if closer is None:
            return None
        return text[body_start:closer.start()]
    return None


def space_delimited_fields(text, leaders, separator_re, tail):
    """Match a lazy dot field after a finite set of greedy leading grammars.

    Separators consume a maximal whitespace run (group 1) and a fixed grammar.
    Test each run once, rather than repartitioning it between a leading space
    quantifier, a dot capture, and the separator. ``tail`` must also scan
    monotonically or use only a fixed/bounded grammar.
    """
    separators = list(separator_re.finditer(text))
    for leader_re, minimum_space in leaders:
        leader = leader_re.match(text)
        if leader is None:
            continue
        start = leader.end()
        newline = text.find("\n", start)
        line_end = len(text) if newline < 0 else newline
        for separator in separators:
            end = separator.start(1)
            if start < end <= line_end:
                remainder = tail(separator)
                if remainder is not None:
                    return (text[start:end], *remainder)
        # A greedy leading run may give back a dot character when the field
        # consists entirely of whitespace. Only the last non-LF character
        # that leaves the mandatory separator can win; do not retry suffixes.
        run_start = start
        while run_start and text[run_start - 1].isspace():
            run_start -= 1
        earliest = run_start + minimum_space
        for separator in separators:
            if separator.end(1) != start:
                continue
            end = start - (1 if separator.group(1) else 0)
            candidate = end - 1
            while candidate >= earliest and text[candidate] == "\n":
                candidate -= 1
            if candidate >= earliest:
                remainder = tail(separator)
                if remainder is not None:
                    return (text[candidate:candidate + 1], *remainder)
    return None


def terminal_dot_field(text, start, *, punctuation=False, last_newline=None):
    """Read a whitespace-led dot field ending at Python's dollar boundary."""
    if start >= len(text) or not text[start].isspace():
        return None
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    end = len(text) - (1 if text.endswith("\n") else 0)
    if cursor >= end:
        cursor = end - 1
        while cursor > start and text[cursor] == "\n":
            cursor -= 1
        if cursor <= start or text[cursor] == "\n":
            return None
    # Newlines before the field can be leading whitespace; newlines inside
    # the dot capture cannot be consumed. The last LF is a constant-time veto.
    if last_newline is None:
        last_newline = text.rfind("\n", 0, end)
    if last_newline >= cursor:
        return None
    capture_end = end
    if punctuation:
        while capture_end > cursor and text[capture_end - 1].isspace():
            capture_end -= 1
        if capture_end > cursor and text[capture_end - 1] in ".!?":
            capture_end -= 1
        else:
            capture_end = end
        if capture_end == cursor:
            capture_end = end
    return (text[cursor:capture_end],)
