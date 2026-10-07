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


async def stream_reply(candidates, messages, emit, *, max_tokens=1536, require_complete_response=False):
    from src.llm_core import stream_llm
    error = 'No usable reply returned'
    for url, model, headers in candidates:
        raw = ''
        visible = ''
        await emit({'type': 'reply', 'text': ''})
        try:
            event_is_error = False
            async for chunk in stream_llm(url, model, messages, headers=headers,
                    temperature=0.3, max_tokens=max_tokens, timeout=120, thinking_mode='off',
                    **({'require_complete_response': True} if require_complete_response else {})):
                for line in chunk.splitlines():
                    if line.startswith('event:'):
                        event_is_error = line[6:].strip() == 'error'
                        continue
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if not data or data == '[DONE]':
                        continue
                    event = json.loads(data)
                    if event_is_error or event.get('error'):
                        raise RuntimeError('Model did not return a completed reply')
                    event_is_error = False
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
