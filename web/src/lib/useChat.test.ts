import { describe, expect, it } from 'vitest'
import { historyToMessages } from './useChat'

describe('historyToMessages', () => {
  it('restores attachments, agent tools, clarification prompts, and continuation state', () => {
    const messages = historyToMessages([
      { role: 'user', content: '', metadata: { attachments: [{ id: 'file-1', name: 'brief.pdf', mime: 'application/pdf', size: 42, vision_text: 'OCR result' }] } },
      { role: 'assistant', content: 'Which direction?', metadata: {
        model: 'actual-model', requested_model: 'requested-model', thinking: 'considering', rounds_exhausted: 12,
        ask_user: { question: 'Which direction?', options: [{ label: 'A', description: 'First' }, { label: 'B' }] },
        tool_events: [{ tool: 'bash', command: 'pwd', output: '/tmp', exit_code: 0, round: 1 }],
        web_sources: [{ url: 'https://example.com', title: 'Example' }],
      } },
    ])
    expect(messages[0].attachments).toEqual([expect.objectContaining({ id: 'file-1', name: 'brief.pdf' })])
    expect(messages[0].attachments?.[0].visionText).toBe('OCR result')
    expect(messages[1]).toEqual(expect.objectContaining({
      model: 'requested-model', modelActual: 'actual-model', reasoning: 'considering',
      askUser: expect.objectContaining({ options: [expect.objectContaining({ label: 'A' }), expect.objectContaining({ label: 'B' })] }),
      notice: expect.objectContaining({ kind: 'warning', continuePrompt: expect.any(String) }),
      tools: [expect.objectContaining({ name: 'bash', output: '/tmp', running: false })],
    }))
  })

  it('restores richer timing and token metrics', () => {
    const [message] = historyToMessages([{ role: 'assistant', content: 'Done', metadata: { input_tokens: 10, output_tokens: 20, total_tokens: 30, context_tokens: 40, prep_seconds: 1.2, model_wait_seconds: 0.4, response_seconds: 2.5, edited: true } }])
    expect(message.metrics).toEqual(expect.objectContaining({ tokens_in: 10, tokens_out: 20, tokens_total: 30, context_tokens: 40, prep_seconds: 1.2, model_wait_seconds: 0.4, response_seconds: 2.5 }))
    expect(message.edited).toBe(true)
  })

  it('reconstructs interleaved agent rounds from round_texts + tool_events', () => {
    // Mirrors a real saved agent turn: raw content carries the create_document
    // fence; round_texts are the cleaned per-round texts; tools carry 1-based rounds.
    const [message] = historyToMessages([{ role: 'assistant', content: '```create_document\nColor Demo\nhtml\n<h1>x</h1>\n```\n\nDone — created it.The title was wrong.Fixed.', metadata: {
      round_texts: ['Done — created it.', 'The title was wrong.', 'Fixed.'],
      tool_events: [
        { tool: 'create_document', command: 'Color Demo', output: 'created', round: 1, doc_id: 'color-demo' },
        { tool: 'edit_document', output: 'edited', round: 2, diff: { text: '+<title>', file: 'index.html', added: 1 } },
      ],
    } }])
    expect(message.rounds).toHaveLength(3)
    expect(message.rounds?.[0]).toEqual({ text: 'Done — created it.', tools: [expect.objectContaining({ name: 'create_document', docId: 'color-demo' })] })
    expect(message.rounds?.[1]).toEqual({ text: 'The title was wrong.', tools: [expect.objectContaining({ name: 'edit_document', diff: expect.objectContaining({ file: 'index.html' }) })] })
    expect(message.rounds?.[2]).toEqual({ text: 'Fixed.', tools: [] })
  })

  it('leaves a plain reply (no tools) without rounds so it renders flat', () => {
    const [message] = historyToMessages([{ role: 'assistant', content: 'Just a chat reply.', metadata: { model: 'm' } }])
    expect(message.rounds).toBeUndefined()
  })
})
