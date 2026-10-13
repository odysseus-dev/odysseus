"""Stream only the explicitly delimited email body, never model reasoning."""
import json
import re


def reply_body(raw, *, complete=False):
    match = re.search(r'<<<\s*REPLY\s*>>>', raw, re.I)
    if not match:
        return ''
    body = raw[match.end():]
    end = re.search(r'<<<\s*END\s*>>>', body, re.I)
    if complete and not end:
        return ''
    body = body[:end.start()] if end else body.split('<', 1)[0]
    if re.search(r'</?think\b', body, re.I):
        return ''
    return body.strip()


async def stream_reply(candidates, messages, emit, *, max_tokens=1536):
    from src.llm_core import stream_llm
    error = 'No usable reply returned'
    for url, model, headers in candidates:
        raw = ''
        visible = ''
        await emit({'type': 'reply', 'text': ''})
        try:
            async for chunk in stream_llm(url, model, messages, headers=headers,
                    temperature=0.3, max_tokens=max_tokens, timeout=120, thinking_mode='off'):
                for line in chunk.splitlines():
                    if not line.startswith('data: ') or line[6:] == '[DONE]':
                        continue
                    event = json.loads(line[6:])
                    if event.get('error'):
                        raise RuntimeError(event['error'])
                    if event.get('thinking'):
                        continue
                    raw += event.get('delta') or ''
                    body = reply_body(raw)
                    if body != visible:
                        visible = body
                        await emit({'type': 'reply', 'text': body})
            if not reply_body(raw, complete=True):
                raise ValueError('Model returned analysis or an incomplete reply, not a finished email')
            return raw, model
        except Exception as exc:
            error = str(exc)
            await emit({'type': 'reply', 'text': ''})
    raise ValueError(error)
