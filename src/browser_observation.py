"""Readable, bounded browser evidence without duplicate reference dictionaries."""
import json
import re


def compact_browser_observation(value, budget=8000):
    notices, pages = [], []

    def visit(item):
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            if item.get('error'):
                notices.append('Error: ' + str(item['error'])[:1000])
            if item.get('exit_code') not in (None, 0):
                notices.append('Exit code: ' + str(item['exit_code']))
            if item.get('success') is False:
                notices.append('Browser command failed.')
            snapshot = item.get('snapshot') or item.get('text')
            if item.get('title'):
                notices.append('Title: ' + str(item['title']))
            url = item.get('url') or item.get('origin')
            if isinstance(snapshot, str) and snapshot.strip():
                # Snapshot text already contains labels and refs in DOM order.
                # The refs mapping repeats them and buries menus in raw JSON.
                lines = [line for line in snapshot.splitlines()
                         if not re.fullmatch(r'\s*-?\s*generic(?:\s+\[ref=e\d+\])?:?\s*', line)]
                pages.append(('URL: ' + str(url) + '\n' if url else '') + '\n'.join(lines))
            elif url:
                notices.append('URL: ' + str(url))
            for key in ('result', 'output'):
                if key in item:
                    visit(item[key])
        elif isinstance(item, str):
            try:
                parsed = json.loads(item)
            except (ValueError, TypeError):
                # CLI status text precedes the JSON post-interaction state.
                parts = re.split(r'\n\n\[(?:post-[^\]]+|page state after failed [^\]]+)\]\n', item)
                if len(parts) > 1:
                    for part in parts:
                        visit(part)
                elif item.strip():
                    notices.append(item.strip())
            else:
                if isinstance(parsed, (dict, list)):
                    visit(parsed)
                else:
                    notices.append(str(parsed))

    visit(value)
    if not notices and not pages:
        notices.append(json.dumps(value, ensure_ascii=False))
    prefix = '\n'.join(dict.fromkeys(notices))[:2000]
    body = pages[-1] if pages else ''
    if not pages:
        prefix = '\n'.join(dict.fromkeys(notices))
    text = (prefix + '\n\n' + body).strip()
    if len(text) <= budget:
        return text
    hint = '\n[Page observation shortened at line boundaries. Use a focused snapshot/read to inspect omitted content; do not guess refs.]\n'
    room = budget - len(hint)
    head = text[:room * 2 // 3].rsplit('\n', 1)[0]
    tail = text[-room // 3:].split('\n', 1)[-1]
    return head + hint + tail
