"""Bounded, extractive search observations; never generate source claims."""
import math
import re

_FILLER = frozenset('the and for with from this that what which how find search compare explain latest current recent official source sources documentation document please about'.split())


def bounded_search_observation(output, budget=8000):
    """Budget all fetched sources before any transport-level prefix truncation."""
    if len(output) <= budget:
        return output
    pattern = re.compile(r'\n(\[CONTENT(?: \d+)?\] From: [^\n]+\nTitle: [^\n]*\n-+\n)')
    matches = list(pattern.finditer(output))
    if not matches:
        return output
    source_match = re.search(r'```sources\n.*?```', output, re.DOTALL)
    query_match = re.search(r'^Query: .*$', output, re.MULTILINE)
    prefix = '\n'.join(match.group(0) for match in (source_match, query_match) if match)
    suffix = '\n[Excerpts shortened across sources; use web_fetch on a source URL for full details.]'
    headers = [match.group(1) for match in matches]
    room = budget - len(prefix) - len(suffix) - sum(len(h) + 2 for h in headers) - 2
    if room < 100 * len(matches):
        return output  # Caller retains its hard cap for exceptional metadata.
    per_page = room // len(matches)
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
        body = output[match.end():end]
        body = re.split(r'\n(?:Key Points:|TL;DR:|Important Quotes:|Data / Statistics:|={20,}|<!-- SOURCES:)', body, maxsplit=1)[0].strip()
        if len(body) > per_page:
            body = search_excerpt(body, query_match.group(0) if query_match else '', per_page)
        blocks.append(headers[index] + body)
    return prefix + '\n\n' + '\n\n'.join(blocks) + suffix


def search_excerpt(text: str, query: str, max_chars: int) -> str:
    """Keep the opening plus relevant, non-overlapping literal page excerpts.

    Offsets are selected from the original text, so qualifiers and negation
    within a passage are preserved. Explicit omission markers prevent these
    disjoint excerpts from masquerading as a continuous quotation.
    """
    if len(text) <= max_chars:
        return text
    marker = '\n[...text omitted; fetch source for full context...]\n'
    if max_chars < 200:
        return text[:max_chars]
    clean_query = re.sub(r'(?<!\S)-?(?:site|filetype):\S+', '', query, flags=re.I)
    terms = list(dict.fromkeys(
        t.casefold() for t in re.findall(r'\w+', clean_query)
        if len(t) >= 2 and t.casefold() not in _FILLER and not t.isdigit()
    ))[:24]
    patterns = [re.compile(r'\b' + re.escape(term) + r'\b', re.I) for term in terms]
    hits, counts = [], []
    for pattern in patterns:
        anchors, count = [], 0
        for match in pattern.finditer(text):
            count += 1
            if len(anchors) < 24:
                anchors.append(match)
        hits.append(anchors)
        counts.append(count)
    if not any(hits):
        return text[:max_chars - len(marker)] + marker
    # Preserve page-level scope/age disclaimers rather than showing only the
    # matching section. The rest of the budget is shared by up to two spans.
    lead_end = min(240, max_chars // 5)
    boundary = text.rfind(' ', 0, lead_end)
    if boundary > 0:
        lead_end = boundary
    remaining = max_chars - lead_end - 3 * len(marker)
    width = max(1, remaining // 2)
    weights = [1 / (1 + math.log1p(count)) for count in counts]
    candidates = {}
    for matches in hits:
        for match in matches[:24]:
            start = max(lead_end, min(len(text) - width, match.start() - width // 3))
            if start > lead_end:
                boundary = text.find(' ', start, min(len(text), start + 60))
                if boundary >= 0:
                    start = boundary + 1
            end = min(len(text), start + width)
            boundary = text.rfind(' ', start, end)
            if boundary > start:
                end = boundary
            if end <= start:
                continue
            passage = text[start:end]
            coverage = [bool(pattern.search(passage)) for pattern in patterns]
            score = sum(weight for weight, matched in zip(weights, coverage) if matched)
            candidates[(start, end)] = (score, {i for i, matched in enumerate(coverage) if matched})
    selected = [(0, lead_end)]
    covered = set()
    for (start, end), (score, matched) in sorted(candidates.items(), key=lambda item: (-item[1][0], item[0][0])):
        if score <= 0 or any(start < b and end > a for a, b in selected):
            continue
        if selected[1:] and not matched - covered:
            continue
        selected.append((start, end))
        covered.update(matched)
        if len(selected) == 3:
            break
    if len(selected) == 2:
        # Spend unused space on context around the best passage, rather than
        # filling a second slot with a weaker repetition of the same terms.
        start, end = selected[1]
        end = min(len(text), start + max_chars - lead_end - 2 * len(marker))
        boundary = text.rfind(' ', start, end)
        if boundary > start:
            end = boundary
        selected[1] = (start, end)
    selected.sort()
    output = marker.join(text[start:end] for start, end in selected)
    if selected[-1][1] < len(text):
        output += marker
    return output[:max_chars]
