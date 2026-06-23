import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Message } from './Message'

vi.mock('@/api/voice', () => ({ useVoiceCaps: () => ({ data: { tts: false } }), speak: vi.fn() }))

describe('Message parity states', () => {
  afterEach(() => vi.restoreAllMocks())
  it('renders persisted attachment-only user turns', () => {
    render(<Message m={{ role: 'user', content: '', attachments: [{ id: 'f1', name: 'brief.pdf', size: 2048 }] }} />)
    expect(screen.getByText('brief.pdf')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
  })

  it('lets the user continue an exhausted agent run', () => {
    const onRespond = vi.fn()
    render(<Message m={{ role: 'assistant', content: 'Partial', notice: { kind: 'warning', text: 'Not finished', continuePrompt: 'Continue exactly.' } }} onRespond={onRespond} />)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    expect(onRespond).toHaveBeenCalledWith('Continue exactly.')
  })

  it('sends a clarification option as the next user turn', () => {
    const onRespond = vi.fn()
    render(<Message m={{ role: 'assistant', content: 'Choose', askUser: { options: [{ label: 'Fast', description: 'Ship now' }, { label: 'Careful' }] } }} onRespond={onRespond} />)
    fireEvent.click(screen.getByRole('button', { name: /fast/i }))
    expect(onRespond).toHaveBeenCalledWith('Fast')
  })

  it('exposes assistant edit, rewrite, fork, and delete actions', () => {
    const edit = vi.fn(), rewrite = vi.fn(), fork = vi.fn(), remove = vi.fn()
    render(<Message m={{ role: 'assistant', content: 'A long answer' }} onEdit={edit} onRewrite={rewrite} onFork={fork} onDelete={remove} />)
    fireEvent.click(screen.getByTitle('More message actions'))
    fireEvent.click(screen.getByRole('button', { name: /make shorter/i }))
    expect(rewrite).toHaveBeenCalledWith(expect.stringContaining('shorter'))
    fireEvent.click(screen.getByTitle('More message actions'))
    fireEvent.click(screen.getByRole('button', { name: /edit response/i }))
    expect(edit).toHaveBeenCalled()
  })

  it('loads and saves editable image OCR text', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ text: 'Detected words' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    render(<Message m={{ role: 'user', content: '', attachments: [{ id: 'img-1', name: 'photo.png', mime: 'image/png' }] }} />)
    fireEvent.click(screen.getByTitle('Review image description / OCR'))
    expect(await screen.findByDisplayValue('Detected words')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('Detected words'), { target: { value: 'Corrected words' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save text' }))
    expect(fetchMock.mock.calls[1][0]).toBe('/api/upload/img-1/vision')
    expect(String((fetchMock.mock.calls[1][1] as RequestInit).body)).toContain('Corrected words')
  })
})
