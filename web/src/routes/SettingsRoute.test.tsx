import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AccountSecurity } from './SettingsRoute'

const setup = vi.fn(async () => ({ qr_code: 'data:image/png;base64,AA==' }))
const confirm = vi.fn(async (code: string) => { void code; return { ok: true, backup_codes: ['BACKUP-ONE', 'BACKUP-TWO'] } })

vi.mock('@/api/auth', () => ({
  useTwoFAStatus: () => ({ data: { enabled: false } }),
  setup2FA: () => setup(), confirm2FA: (code: string) => confirm(code),
  disable2FA: vi.fn(), changePassword: vi.fn(),
  useAuthStatus: () => ({ data: {} }), useUsers: () => ({ data: [] }), useUserMutations: () => ({}),
  logout: vi.fn(), setOpenSignup: vi.fn(),
}))

describe('2FA enrollment', () => {
  it('shows backup codes and requires a saved acknowledgment before dismissal', async () => {
    render(<QueryClientProvider client={new QueryClient()}><AccountSecurity /></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Enable 2FA' }))
    await screen.findByAltText('2FA QR')
    fireEvent.change(screen.getByPlaceholderText('6-digit code'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('BACKUP-ONE')).toBeInTheDocument()
    const done = screen.getByRole('button', { name: 'Done' })
    expect(done).toBeDisabled()
    fireEvent.click(screen.getByLabelText('I saved these recovery codes.'))
    expect(done).toBeEnabled()
    fireEvent.click(done)
    await waitFor(() => expect(screen.queryByText('BACKUP-ONE')).not.toBeInTheDocument())
  })
})
