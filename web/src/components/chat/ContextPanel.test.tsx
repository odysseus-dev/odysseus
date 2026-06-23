import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanel } from '@/stores/panel'
import { ContextPanel } from './ContextPanel'

const update = vi.hoisted(() => ({
  mutate: vi.fn((_input, options?: { onSuccess?: () => void }) => options?.onSuccess?.()),
  isPending: false,
}))

vi.mock('@/api/documents', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/documents')>(),
  useDocMutations: () => ({ update }),
}))
vi.mock('./ShareMenu', () => ({ ShareMenu: () => null }))

describe('document context panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    usePanel.setState({
      open: true,
      kind: 'doc',
      files: [],
      doc: {
        title: 'Draft',
        language: 'markdown',
        content: 'Hello world',
        docId: 'doc-1',
        selections: [],
        suggestions: [{ id: 's1', find: 'world', replace: 'Odysseus', reason: 'Use the product name' }],
      },
    })
  })

  it('applies AI suggestions and pins editor selections for the next chat', async () => {
    render(<MemoryRouter><ContextPanel /></MemoryRouter>)
    expect(screen.getByText('Use the product name')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(update.mutate).toHaveBeenCalledWith(
      { id: 'doc-1', content: 'Hello Odysseus' },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
    await waitFor(() => expect(usePanel.getState().doc?.content).toBe('Hello Odysseus'))
    expect(usePanel.getState().doc?.suggestions).toEqual([])

    fireEvent.click(screen.getByTitle('Edit'))
    const editor = screen.getByRole('textbox') as HTMLTextAreaElement
    editor.setSelectionRange(6, 14)
    fireEvent.select(editor)
    fireEvent.click(screen.getByRole('button', { name: /pin selection/i }))
    expect(usePanel.getState().doc?.selections).toEqual([
      expect.objectContaining({ text: 'Odysseus', startLine: 1, endLine: 1 }),
    ])
  })
})
