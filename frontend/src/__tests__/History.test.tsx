import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import History from '../History'

const DONE_JOB = {
  id: 'job-done-1',
  subject: 'Monte Carlo Simulation Tool',
  status: 'done' as const,
  verdict: 'Worth Exploring',
  score: 7.9,
  error_message: null,
  degraded_sources: [],
  created_at: '2025-01-01T00:00:00Z',
  completed_at: '2025-01-01T01:00:00Z',
}

const RUNNING_JOB = {
  id: 'job-running-1',
  subject: 'Running Research',
  status: 'running' as const,
  verdict: null,
  score: null,
  error_message: null,
  degraded_sources: [],
  created_at: '2025-01-02T00:00:00Z',
  completed_at: null,
}

describe('History', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows loading indicator while fetching', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    render(<History onSelectJob={vi.fn()} onBack={vi.fn()} />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows no jobs message when list is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ json: async () => [] }),
    )
    render(<History onSelectJob={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByText(/no jobs/i)).toBeInTheDocument(),
    )
  })

  it('renders a card for each job with subject text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        json: async () => [DONE_JOB, RUNNING_JOB],
      }),
    )
    render(<History onSelectJob={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Monte Carlo Simulation Tool')).toBeInTheDocument()
      expect(screen.getByText('Running Research')).toBeInTheDocument()
    })
  })

  it('renders verdict pill for done jobs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ json: async () => [DONE_JOB] }),
    )
    render(<History onSelectJob={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByText(/worth exploring/i)).toBeInTheDocument(),
    )
  })

  it('renders download links for done jobs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ json: async () => [DONE_JOB] }),
    )
    render(<History onSelectJob={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(
        screen.getByTestId('history-download-pdf-job-done-1'),
      ).toBeInTheDocument()
      expect(
        screen.getByTestId('history-download-docx-job-done-1'),
      ).toBeInTheDocument()
    })
  })

  it('renders degraded coverage banner when sources failed', async () => {
    const degradedJob = {
      ...DONE_JOB,
      id: 'job-degraded',
      degraded_sources: ['perplexity', 'xai'],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ json: async () => [degradedJob] }),
    )
    render(<History onSelectJob={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() =>
      expect(
        screen.getByTestId('degraded-coverage-banner'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText(/perplexity/)).toBeInTheDocument()
  })

  it('calls onSelectJob with job id when card is clicked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ json: async () => [DONE_JOB] }),
    )
    const onSelectJob = vi.fn()
    render(<History onSelectJob={onSelectJob} onBack={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByText('Monte Carlo Simulation Tool')).toBeInTheDocument(),
    )
    await userEvent.click(
      screen.getByTestId('history-card-job-done-1'),
    )

    expect(onSelectJob).toHaveBeenCalledWith('job-done-1')
  })

  it('calls onBack when New Job button is clicked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ json: async () => [] }),
    )
    const onBack = vi.fn()
    render(<History onSelectJob={vi.fn()} onBack={onBack} />)

    await screen.findByText(/no jobs/i)
    await userEvent.click(screen.getByRole('button', { name: /new job/i }))

    expect(onBack).toHaveBeenCalledOnce()
  })
})
