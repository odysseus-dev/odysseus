import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentPluginsSection, ContactsSection, EmailAccountsSection } from './IntegrationsExtra'

const calls = vi.hoisted(() => ({
  add: { mutateAsync: vi.fn(async () => ({})), isPending: false },
  update: { mutateAsync: vi.fn(async () => ({})), isPending: false },
  remove: { mutate: vi.fn(), isPending: false },
  saveConfig: { mutate: vi.fn(), isPending: false },
  token: { mutate: vi.fn((_input, options?: { onSuccess?: (result: { token: string }) => void }) => options?.onSuccess?.({ token: 'secret-token' })), isPending: false },
}))

vi.mock('@/api/accounts', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/accounts')>(),
  useContactsCount: () => ({ data: 1 }),
  useContactList: () => ({ data: [{ uid: 'c1', name: 'Ada', emails: ['ada@example.com'], phones: ['123'], address: 'London' }] }),
  useCardDavConfig: () => ({ data: { url: 'https://dav.example/contacts', username: 'ada', password: '***' } }),
  useContactMutations: () => calls,
  clearContacts: vi.fn(),
  useEmailAccounts: () => ({ data: [{ id: 'gmail-1', name: 'Work Gmail', from_address: 'ada@gmail.com', imap_host: 'imap.gmail.com', is_default: true }] }),
  useEmailAccountMutations: () => ({ remove: { mutate: vi.fn() }, setDefault: { mutate: vi.fn() }, create: { mutate: vi.fn(), isPending: false }, update: { mutate: vi.fn(), isPending: false } }),
  useEmailStyle: () => ({ data: {} }),
}))

vi.mock('@/api/tokens', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/tokens')>(),
  useTokenMutations: () => ({ create: calls.token }),
}))

describe('contacts and CardDAV settings', () => {
  it('supports search, add, edit, delete, and CardDAV configuration', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<ContactsSection />)
    expect(screen.getByText('Ada')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Search contacts'), { target: { value: 'missing' } })
    expect(screen.getByText('No contacts found.')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Search contacts'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: /add contact/i }))
    fireEvent.change(screen.getByPlaceholderText('Name'), { target: { value: 'Grace' } })
    fireEvent.change(screen.getByPlaceholderText('Email'), { target: { value: 'grace@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save contact' }))
    expect(calls.add.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ name: 'Grace', email: 'grace@example.com' }))
    fireEvent.click(screen.getByTitle('Edit contact'))
    expect(screen.getByDisplayValue('ada@example.com')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Delete contact'))
    expect(calls.remove.mutate).toHaveBeenCalledWith('c1')
    fireEvent.click(screen.getByRole('button', { name: 'Save CardDAV' }))
    expect(calls.saveConfig.mutate).toHaveBeenCalledWith(expect.objectContaining({ carddav_url: 'https://dav.example/contacts', carddav_username: 'ada' }), expect.anything())
  })

  it('exposes Google OAuth and dedicated Codex/Claude plugin setup', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const { unmount } = render(<EmailAccountsSection />)
    fireEvent.click(screen.getByTitle('Connect Google Workspace'))
    expect(open).toHaveBeenCalledWith('/api/email/oauth/google/authorize?account_id=gmail-1', '_blank', 'noopener')
    unmount()

    render(<AgentPluginsSection />)
    expect(screen.getByText('Codex Agent')).toBeInTheDocument()
    expect(screen.getByText('Claude Code')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Create token & setup' })[0])
    expect(calls.token.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Codex Agent plugin', scopes: expect.stringContaining('documents:write') }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
    expect(screen.getByText(/ODYSSEUS_API_TOKEN='secret-token'/)).toBeInTheDocument()
  })
})
