import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SignIn from '../SignIn'

describe('SignIn', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders password input and submit button', () => {
    render(<SignIn onAuthenticated={vi.fn()} />)
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows error and does not call fetch when password is empty', async () => {
    render(<SignIn onAuthenticated={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() =>
      expect(screen.getByText(/password is required/i)).toBeInTheDocument(),
    )
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('calls onAuthenticated when login succeeds', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true }),
    )
    const onAuthenticated = vi.fn()
    render(<SignIn onAuthenticated={onAuthenticated} />)

    await userEvent.type(screen.getByLabelText(/password/i), 'correct-password')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce())
  })

  it('shows error message on wrong password (non-ok response)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 401 }),
    )
    const onAuthenticated = vi.fn()
    render(<SignIn onAuthenticated={onAuthenticated} />)

    await userEvent.type(screen.getByLabelText(/password/i), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() =>
      expect(screen.getByText(/invalid password/i)).toBeInTheDocument(),
    )
    expect(onAuthenticated).not.toHaveBeenCalled()
  })

  it('shows connection error when fetch throws', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Network Error')),
    )
    render(<SignIn onAuthenticated={vi.fn()} />)

    await userEvent.type(screen.getByLabelText(/password/i), 'any-password')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() =>
      expect(screen.getByText(/could not connect/i)).toBeInTheDocument(),
    )
  })

  it('disables submit button while loading', async () => {
    let resolveFetch!: (v: unknown) => void
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(new Promise((r) => { resolveFetch = r })),
    )
    render(<SignIn onAuthenticated={vi.fn()} />)

    await userEvent.type(screen.getByLabelText(/password/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled()
    resolveFetch({ ok: true })
  })
})
