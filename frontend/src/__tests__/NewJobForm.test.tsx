import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import NewJobForm from '../NewJobForm'

describe('NewJobForm', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders subject textarea, time window select, and submit button', () => {
    render(<NewJobForm onJobCreated={vi.fn()} />)
    expect(screen.getByLabelText(/subject/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/time window/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start research/i })).toBeInTheDocument()
  })

  it('calls onJobCreated with the returned job ID on successful submit', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ id: 'new-job-abc-123' }),
      }),
    )
    const onJobCreated = vi.fn()
    render(<NewJobForm onJobCreated={onJobCreated} />)

    await userEvent.type(screen.getByLabelText(/subject/i), 'Test Research Subject')
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    await waitFor(() =>
      expect(onJobCreated).toHaveBeenCalledWith('new-job-abc-123'),
    )
  })

  it('shows 409 error message when a job is already running', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: 'A job is already running.' }),
      }),
    )
    const onJobCreated = vi.fn()
    render(<NewJobForm onJobCreated={onJobCreated} />)

    await userEvent.type(screen.getByLabelText(/subject/i), 'Conflicting Job')
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    await waitFor(() =>
      expect(screen.getByText(/already running/i)).toBeInTheDocument(),
    )
    expect(onJobCreated).not.toHaveBeenCalled()
  })

  it('shows generic error when json parsing fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => { throw new Error('not JSON') },
      }),
    )
    render(<NewJobForm onJobCreated={vi.fn()} />)

    await userEvent.type(screen.getByLabelText(/subject/i), 'Failing Job')
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    await waitFor(() =>
      expect(screen.getByText(/failed to start job/i)).toBeInTheDocument(),
    )
  })

  it('reveals tickers field only when include_public_market is checked', async () => {
    render(<NewJobForm onJobCreated={vi.fn()} />)

    expect(screen.queryByLabelText(/tickers/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByLabelText(/include public market/i))

    expect(screen.getByLabelText(/tickers/i)).toBeInTheDocument()

    await userEvent.click(screen.getByLabelText(/include public market/i))

    expect(screen.queryByLabelText(/tickers/i)).not.toBeInTheDocument()
  })

  it('sends correct payload including tickers when include_public_market is true', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: 'job-with-tickers' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<NewJobForm onJobCreated={vi.fn()} />)

    await userEvent.type(screen.getByLabelText(/subject/i), 'Market Research')
    await userEvent.click(screen.getByLabelText(/include public market/i))
    await userEvent.type(screen.getByLabelText(/tickers/i), 'AAPL,MSFT')
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.include_public_market).toBe(true)
    expect(body.tickers).toBe('AAPL,MSFT')
  })
})
