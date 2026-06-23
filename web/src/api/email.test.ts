import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchLiveEmailUnreadCount } from './email'

describe('fetchLiveEmailUnreadCount', () => {
  afterEach(() => vi.restoreAllMocks())

  it('polls the live unread inbox and returns the folder-wide total', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ total: 7, emails: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await expect(fetchLiveEmailUnreadCount()).resolves.toBe(7)
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/email/list?'), expect.anything())
    expect(String(fetchMock.mock.calls[0][0])).toContain('filter=unread')
  })
})
