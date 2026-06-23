import { describe, expect, it, vi } from 'vitest'
import { readSse, SseResponseError, StreamInterruptedError } from './sse'

function stream(...chunks: string[]) {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

describe('readSse', () => {
  it('parses complete event records split across chunks', async () => {
    const onEvent = vi.fn()
    await readSse(stream('event: tool_progress\r\ndata: {"tail":"hel', 'lo"}\r\n\r\ndata: [DONE]\r\n\r\n'), onEvent)
    expect(onEvent).toHaveBeenCalledWith({ type: 'tool_progress', tail: 'hello' })
  })

  it('rejects a connection that closes without DONE', async () => {
    await expect(readSse(stream('data: {"delta":"partial"}\n\n'), vi.fn())).rejects.toBeInstanceOf(StreamInterruptedError)
  })

  it('surfaces backend error events instead of silently completing', async () => {
    await expect(readSse(stream('event: error\ndata: {"error":"provider failed","status":500}\n\ndata: [DONE]\n\n'), vi.fn()))
      .rejects.toEqual(expect.objectContaining<SseResponseError>({ name: 'SseResponseError', message: 'provider failed', status: 500 }))
  })
})
