import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useComposer } from '@/stores/composer'
import { useChat } from './useChat'
import { createSession } from '@/api/sessions'
import { streamChat } from '@/lib/sse'

const historyState = vi.hoisted(() => ({ history: undefined as undefined | { history: unknown[] } }))

vi.mock('@/api/sessions', () => ({
  createSession: vi.fn(async () => ({ id: 'new-session', name: 'New chat', model: 'model' })),
  useHistory: () => ({ data: historyState.history }),
}))
vi.mock('@/lib/sse', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/sse')>()
  return { ...actual, streamChat: vi.fn(async (_form, onEvent) => { await onEvent({ type: 'message_saved', id: 'assistant-1' }) }), streamResume: vi.fn() }
})

const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter><QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider></MemoryRouter>

describe('useChat attachment behavior', () => {
  beforeEach(() => {
    historyState.history = undefined
    useComposer.setState({ model: 'model', endpointId: 'endpoint', endpointUrl: 'http://model', groupActive: false, incognito: false })
  })
  afterEach(() => { vi.clearAllMocks(); vi.restoreAllMocks() })

  it('creates and streams an attachment-only chat', async () => {
    const { result } = renderHook(() => useChat(), { wrapper })
    await act(() => result.current.send('', ['file-1'], undefined, { attachments: [{ id: 'file-1', name: 'brief.pdf' }] }))
    expect(createSession).toHaveBeenCalled()
    const form = vi.mocked(streamChat).mock.calls[0][0]
    expect(form.get('message')).toBe('')
    expect(form.get('attachments')).toBe(JSON.stringify(['file-1']))
    expect(result.current.messages[0]).toEqual(expect.objectContaining({ role: 'user', attachments: [expect.objectContaining({ id: 'file-1' })] }))
  })

  it('preserves persisted attachment IDs during regenerate and edit/resend', async () => {
    historyState.history = { history: [{ role: 'user', content: 'Review', metadata: { attachments: [{ id: 'file-2', name: 'plan.png' }] } }, { role: 'assistant', content: 'Done', metadata: { _db_id: 'a1' } }] }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 404 }))
    const { result } = renderHook(() => useChat('session-1'), { wrapper })
    await waitFor(() => expect(result.current.messages).toHaveLength(2))
    await act(() => result.current.regenerate(0))
    expect(vi.mocked(streamChat).mock.calls.at(-1)?.[0].get('attachments')).toBe(JSON.stringify(['file-2']))
    await act(() => result.current.editResend(0, 'Review again'))
    expect(vi.mocked(streamChat).mock.calls.at(-1)?.[0].get('attachments')).toBe(JSON.stringify(['file-2']))
  })
})
