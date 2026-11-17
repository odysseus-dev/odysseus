export async function readEmailReplyResponse(response, onReply) {
  if (!response.ok) throw new Error(`AI reply service returned HTTP ${response.status}`);
  if (!response.headers.get('content-type')?.includes('text/event-stream')) return response.json();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of frame.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === 'reply' && onReply(event.text) === false) {
            throw new Error('Draft was edited or closed; AI insertion stopped');
          }
          if (event.type === 'result') return event;
        }
      }
      if (done) throw new Error('AI reply connection ended before completion');
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
