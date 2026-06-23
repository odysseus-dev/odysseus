import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useSetBuiltinTools } from './tools'
import type { ReactNode } from 'react'

describe('built-in tools control', () => {
  afterEach(() => vi.restoreAllMocks())

  it('writes the complete disabled tool list', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    const client = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const { result } = renderHook(() => useSetBuiltinTools(), { wrapper })
    await act(() => result.current.mutateAsync([{ id: 'bash', enabled: false }, { id: 'web_search', enabled: true }]))
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ disabled: ['bash'] })
  })
})
