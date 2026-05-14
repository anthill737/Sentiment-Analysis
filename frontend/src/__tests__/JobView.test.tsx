import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import JobView from '../JobView'

// ---------------------------------------------------------------------------
// EventSource mock
// ---------------------------------------------------------------------------

class MockEventSource {
  static lastInstance: MockEventSource | null = null
  onmessage: ((evt: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    MockEventSource.lastInstance = this
  }

  close() {}

  emit(data: string) {
    this.onmessage?.({ data })
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRunningJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'test-job',
    subject: 'Test Research Subject',
    status: 'running' as const,
    current_phase: 'gather' as const,
    verdict: null,
    score: null,
    error_message: null,
    degraded_sources: [] as string[],
    created_at: '2025-01-01T00:00:00Z',
    completed_at: null,
    gather_status: {},
    ...overrides,
  }
}

function makeDoneJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'test-job',
    subject: 'Test Research Subject',
    status: 'done' as const,
    current_phase: null,
    verdict: 'Worth Exploring',
    score: 7.9,
    error_message: null,
    degraded_sources: [] as string[],
    created_at: '2025-01-01T00:00:00Z',
    completed_at: '2025-01-01T01:00:00Z',
    gather_status: {},
    angle_scores: {},
    pursue_reasons: [],
    pass_reasons: [],
    confidence: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('JobView — Gather Phase Cards', () => {
  beforeEach(() => {
    MockEventSource.lastInstance = null
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('renders four gather phase cards when job is in gather phase (AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('gather-card-perplexity')).toBeInTheDocument()
      expect(screen.getByTestId('gather-card-xai')).toBeInTheDocument()
      expect(screen.getByTestId('gather-card-trends')).toBeInTheDocument()
      expect(screen.getByTestId('gather-card-fmp')).toBeInTheDocument()
    })
  })

  it('cards appear without a page refresh when gather phase begins (AC1)', async () => {
    // Start in plan phase, then SSE line triggers gather detection
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'plan' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    // Plan card visible first
    await waitFor(() => screen.getByTestId('plan-card'))

    // SSE line from a gather source transitions cards
    act(() => {
      MockEventSource.lastInstance?.emit('[perplexity] [1/3] angle=market-sizing')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-card-perplexity')).toBeInTheDocument()
    })
  })

  it('updates gather card status text from incoming progress lines (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-perplexity'))

    act(() => {
      MockEventSource.lastInstance?.emit('[perplexity] [3/9] angle=pricing-signals')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-step-perplexity')).toHaveTextContent('3/9')
      expect(screen.getByTestId('gather-angle-perplexity')).toHaveTextContent('pricing-signals')
    })
  })

  it('updates different cards independently from their respective sources (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-perplexity'))

    act(() => {
      MockEventSource.lastInstance?.emit('[perplexity] [2/5] angle=competitive-landscape')
      MockEventSource.lastInstance?.emit('[xai] [1/5] angle=market-sizing')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-angle-perplexity')).toHaveTextContent('competitive-landscape')
      expect(screen.getByTestId('gather-angle-xai')).toHaveTextContent('market-sizing')
    })
  })

  it('shows checkmark (done indicator) when source reaches step === total (AC3)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-perplexity'))

    act(() => {
      const es = MockEventSource.lastInstance!
      es.emit('[perplexity] [1/3] angle=market-sizing')
      es.emit('[perplexity] [2/3] angle=competitive-landscape')
      es.emit('[perplexity] [3/3] angle=adoption-signals')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-done-perplexity')).toBeInTheDocument()
    })

    // Step/angle text should be replaced by done indicator
    expect(screen.queryByTestId('gather-step-perplexity')).not.toBeInTheDocument()
  })

  it('shows error indicator when SSE degraded coverage line names the source (AC4)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-perplexity'))

    act(() => {
      MockEventSource.lastInstance?.emit('[gather] degraded coverage: perplexity failed')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-error-perplexity')).toBeInTheDocument()
    })

    // Done indicator must NOT be shown for an errored source
    expect(screen.queryByTestId('gather-done-perplexity')).not.toBeInTheDocument()
  })

  it('shows error indicator for multiple sources from degraded coverage line (AC4)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-fmp'))

    act(() => {
      MockEventSource.lastInstance?.emit('[gather] degraded coverage: xai, fmp failed')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-error-xai')).toBeInTheDocument()
      expect(screen.getByTestId('gather-error-fmp')).toBeInTheDocument()
    })

    // Other sources should not show error
    expect(screen.queryByTestId('gather-error-perplexity')).not.toBeInTheDocument()
    expect(screen.queryByTestId('gather-error-trends')).not.toBeInTheDocument()
  })

  it('shows error indicator via degraded_sources from polled job API (AC4)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ degraded_sources: ['fmp'] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('gather-error-fmp')).toBeInTheDocument()
    })

    // Healthy sources show no error indicator
    expect(screen.queryByTestId('gather-error-perplexity')).not.toBeInTheDocument()
  })

  it('does not show done indicator for an errored source that reached step === total (AC4 > AC3)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeRunningJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-trends'))

    act(() => {
      const es = MockEventSource.lastInstance!
      // Source emits all steps then the pipeline marks it degraded
      es.emit('[trends] [3/3] angle=adoption-signals')
      es.emit('[gather] degraded coverage: trends failed')
    })

    await waitFor(() => {
      expect(screen.getByTestId('gather-error-trends')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('gather-done-trends')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Degraded Coverage Banner
// ---------------------------------------------------------------------------

describe('JobView — Degraded Coverage Banner', () => {
  beforeEach(() => {
    MockEventSource.lastInstance = null
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('shows yellow banner on live Dashboard View when degraded_sources is non-empty (AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ degraded_sources: ['perplexity'] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('degraded-coverage-banner')).toBeInTheDocument()
    })
  })

  it('banner text names each specific failing source (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ degraded_sources: ['perplexity', 'fmp'] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const banner = screen.getByTestId('degraded-coverage-banner')
      expect(banner.textContent).toMatch(/perplexity/i)
      expect(banner.textContent).toMatch(/fmp/i)
    })
  })

  it('banner persists on Results View after job transitions to done (AC3)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ degraded_sources: ['xai'] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('results-view')).toBeInTheDocument()
      expect(screen.getByTestId('degraded-coverage-banner')).toBeInTheDocument()
    })
  })

  it('does not show banner when degraded_sources is empty (AC5)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ degraded_sources: [] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('gather-card-perplexity'))
    expect(screen.queryByTestId('degraded-coverage-banner')).not.toBeInTheDocument()
  })

  it('does not show banner on Results View when degraded_sources is empty (AC5)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ degraded_sources: [] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('results-view'))
    expect(screen.queryByTestId('degraded-coverage-banner')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Analyze Phase Cards
// ---------------------------------------------------------------------------

describe('JobView — Analyze Phase Cards', () => {
  beforeEach(() => {
    MockEventSource.lastInstance = null
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('analyze step cards appear as SSE lines arrive (AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    // No analyze cards before SSE lines
    await waitFor(() => screen.getByTestId('analyze-cards-container'))
    expect(screen.queryByTestId('analyze-step-card-extract')).not.toBeInTheDocument()

    // Emit extract tag — extract card should appear
    act(() => {
      MockEventSource.lastInstance?.emit('[extract] processing evidence...')
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-step-card-extract')).toBeInTheDocument()
    })
  })

  it('cards appear in sequential order as steps start (AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('analyze-cards-container'))

    act(() => {
      const es = MockEventSource.lastInstance!
      es.emit('[extract] running...')
      es.emit('[cluster] running...')
      es.emit('[score] running...')
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-step-card-extract')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-cluster')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-score')).toBeInTheDocument()
    })

    // Cards before current step should be done, not running
    expect(screen.getByTestId('analyze-done-extract')).toBeInTheDocument()
    expect(screen.getByTestId('analyze-done-cluster')).toBeInTheDocument()
  })

  it('no more than one card is in running state at a time (AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('analyze-cards-container'))

    act(() => {
      const es = MockEventSource.lastInstance!
      es.emit('[extract] processing...')
      es.emit('[cluster] clustering...')
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-step-card-extract')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-cluster')).toBeInTheDocument()
    })

    // extract should be done, cluster should be running
    expect(screen.getByTestId('analyze-done-extract')).toBeInTheDocument()
    expect(screen.queryByTestId('analyze-error-extract')).not.toBeInTheDocument()

    // cluster is the running step — no done indicator yet
    expect(screen.queryByTestId('analyze-done-cluster')).not.toBeInTheDocument()

    // score hasn't started — no card
    expect(screen.queryByTestId('analyze-step-card-score')).not.toBeInTheDocument()
  })

  it('write_sections card shows sections progress text from SSE lines (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('analyze-cards-container'))

    act(() => {
      const es = MockEventSource.lastInstance!
      es.emit('[extract] done')
      es.emit('[cluster] done')
      es.emit('[score] done')
      es.emit("[sections] [1/12] writing 'Market Overview'")
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-step-card-sections')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-sections-progress')).toBeInTheDocument()
    })

    expect(screen.getByTestId('analyze-sections-progress').textContent).toMatch(
      /Market Overview/,
    )
    expect(screen.getByTestId('analyze-sections-progress').textContent).toMatch(
      /section 1 of 12/,
    )
  })

  it('sections progress text updates as more lines arrive (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('analyze-cards-container'))

    act(() => {
      MockEventSource.lastInstance?.emit("[sections] [1/5] writing 'Market Overview'")
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-sections-progress').textContent).toMatch(
        /Market Overview/,
      )
    })

    act(() => {
      MockEventSource.lastInstance?.emit("[sections] [2/5] writing 'Competitive landscape'")
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-sections-progress').textContent).toMatch(
        /Competitive landscape/,
      )
      expect(screen.getByTestId('analyze-sections-progress').textContent).toMatch(
        /section 2 of 5/,
      )
    })
  })

  it('completed step shows checkmark done indicator (AC3)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('analyze-cards-container'))

    act(() => {
      const es = MockEventSource.lastInstance!
      es.emit('[extract] processing...')
      es.emit('[cluster] clustering...')  // causes extract → done
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-done-extract')).toBeInTheDocument()
    })

    // Sections step hasn't started — no card
    expect(screen.queryByTestId('analyze-step-card-sections')).not.toBeInTheDocument()
  })

  it('all 7 analyze step cards appear by end of analyze phase (AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('analyze-cards-container'))

    act(() => {
      const es = MockEventSource.lastInstance!
      es.emit('[extract] step 1')
      es.emit('[cluster] step 2')
      es.emit('[score] step 3')
      es.emit("[sections] [1/3] writing 'Overview'")
      es.emit('[executive] step 5')
      es.emit('[charts] step 6')
      es.emit('[assemble] step 7')
    })

    await waitFor(() => {
      expect(screen.getByTestId('analyze-step-card-extract')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-cluster')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-score')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-sections')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-executive')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-charts')).toBeInTheDocument()
      expect(screen.getByTestId('analyze-step-card-assemble')).toBeInTheDocument()
    })
  })

  it('Phase Timeline node for analyze has active state during analyze phase (AC4)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const analyzeNode = screen.getByTestId('phase-node-analyze')
      expect(analyzeNode.getAttribute('data-phase-state')).toBe('active')
    })

    // Render node should NOT be active yet
    const renderNode = screen.getByTestId('phase-node-render')
    expect(renderNode.getAttribute('data-phase-state')).not.toBe('active')
  })

  it('Phase Timeline transitions to render active only after analyze SSE tags stop (AC4)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() =>
      expect(
        screen.getByTestId('phase-node-analyze').getAttribute('data-phase-state'),
      ).toBe('active'),
    )

    // Emit a render-phase tag — timeline should transition
    act(() => {
      MockEventSource.lastInstance?.emit('[render] rendering report...')
    })

    await waitFor(() => {
      expect(
        screen.getByTestId('phase-node-render').getAttribute('data-phase-state'),
      ).toBe('active')
    })

    expect(
      screen.getByTestId('phase-node-analyze').getAttribute('data-phase-state'),
    ).toBe('completed')
  })
})

// ---------------------------------------------------------------------------
// Results View
// ---------------------------------------------------------------------------

describe('JobView — Results View', () => {
  beforeEach(() => {
    MockEventSource.lastInstance = null
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('renders Results View directly when a completed job is loaded by URL (AC6)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeDoneJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('results-view')).toBeInTheDocument()
    })

    // Live-pipeline elements must not appear for an already-done job
    expect(screen.queryByTestId('render-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('plan-card')).not.toBeInTheDocument()
  })

  it('transitions from live-pipeline view to Results View without page refresh (AC1)', async () => {
    vi.useFakeTimers()

    const mockFetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'render' }),
      })
      .mockResolvedValue({ ok: true, json: async () => makeDoneJob() })

    vi.stubGlobal('fetch', mockFetch)

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    // Run the initial poll + flush the 2 s re-poll timer
    await act(async () => {
      await vi.runAllTimersAsync()
    })

    vi.useRealTimers()

    expect(screen.getByTestId('results-view')).toBeInTheDocument()
    expect(screen.queryByTestId('render-card')).not.toBeInTheDocument()
  })

  it('Verdict Pill shows verdict string and numeric score (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ verdict: 'Worth Exploring', score: 7.9 }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const pill = screen.getByTestId('verdict-pill')
      expect(pill.textContent).toMatch(/Worth Exploring/)
      expect(pill.textContent).toMatch(/7\.9/)
    })
  })

  it('Verdict Pill applies blue color class for "Worth Exploring" verdict (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ verdict: 'Worth Exploring' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const pill = screen.getByTestId('verdict-pill')
      expect(pill.className).toMatch(/blue/)
    })
  })

  it('Verdict Pill applies green color class for pursue-family verdicts (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ verdict: 'Promising — Pursue' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const pill = screen.getByTestId('verdict-pill')
      expect(pill.className).toMatch(/green/)
    })
  })

  it('Verdict Pill applies red color class for "Skip" verdict (AC2)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ verdict: 'Skip' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const pill = screen.getByTestId('verdict-pill')
      expect(pill.className).toMatch(/red/)
    })
  })

  it('Download PDF button has correct href and download attribute (AC3)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeDoneJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const btn = screen.getByTestId('download-pdf-btn')
      expect(btn.getAttribute('href')).toBe('/api/jobs/test-job/download/pdf')
      expect(btn).toHaveAttribute('download')
    })
  })

  it('Download DOCX button has correct href and download attribute (AC3)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeDoneJob() }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      const btn = screen.getByTestId('download-docx-btn')
      expect(btn.getAttribute('href')).toBe('/api/jobs/test-job/download/docx')
      expect(btn).toHaveAttribute('download')
    })
  })

  it('renders horizontal bar chart with exactly six angle score bars (AC4)', async () => {
    const allSixScores = {
      demand: 8,
      competition: 6,
      pricing: 7,
      niches: 9,
      timing: 5,
      features: 7,
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ angle_scores: allSixScores }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('angle-score-chart')).toBeInTheDocument()
      expect(screen.getAllByTestId('score-bar')).toHaveLength(6)
    })
  })

  it('angle score bars display labeled axes with correct score values from API (AC4)', async () => {
    const scores = {
      demand: 8,
      competition: 6,
      pricing: 7,
      niches: 9,
      timing: 5,
      features: 4,
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob({ angle_scores: scores }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('bar-score-demand')).toHaveTextContent('8')
      expect(screen.getByTestId('bar-score-competition')).toHaveTextContent('6')
      expect(screen.getByTestId('bar-score-pricing')).toHaveTextContent('7')
      expect(screen.getByTestId('bar-score-niches')).toHaveTextContent('9')
      expect(screen.getByTestId('bar-score-timing')).toHaveTextContent('5')
      expect(screen.getByTestId('bar-score-features')).toHaveTextContent('4')
    })
  })

  it('pursue reasons card displays at least the top three entries (AC5)', async () => {
    const pursueReasons = [
      'Strong market demand from RIAs',
      'Pricing gap in mid-market',
      'Low competition in niche',
      'Regulatory tailwinds',
    ]

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          makeDoneJob({ pursue_reasons: pursueReasons, pass_reasons: [] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('pursue-reason-0')).toBeInTheDocument()
      expect(screen.getByTestId('pursue-reason-1')).toBeInTheDocument()
      expect(screen.getByTestId('pursue-reason-2')).toBeInTheDocument()
    })

    expect(screen.getByTestId('pursue-reason-0').textContent).toMatch(
      /Strong market demand/,
    )
  })

  it('pass reasons card displays at least the top three entries (AC5)', async () => {
    const passReasons = [
      'High capital requirements',
      'Strong incumbents with lock-in',
      'Slow enterprise sales cycles',
    ]

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          makeDoneJob({ pass_reasons: passReasons, pursue_reasons: [] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('pass-reason-0')).toBeInTheDocument()
      expect(screen.getByTestId('pass-reason-1')).toBeInTheDocument()
      expect(screen.getByTestId('pass-reason-2')).toBeInTheDocument()
    })

    expect(screen.getByTestId('pass-reason-0').textContent).toMatch(/High capital/)
  })

  it('renders gracefully with no crash when pursue and pass reason lists are empty (AC5)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          makeDoneJob({ pursue_reasons: [], pass_reasons: [] }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId('results-view')).toBeInTheDocument()
    })

    // No reason list items rendered — no blank un-labeled cards
    expect(screen.queryByTestId('pursue-reason-0')).not.toBeInTheDocument()
    expect(screen.queryByTestId('pass-reason-0')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Render Phase Card
// ---------------------------------------------------------------------------

describe('JobView — Render Phase Card', () => {
  beforeEach(() => {
    MockEventSource.lastInstance = null
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('AC1: render Phase Timeline node becomes active and prior three nodes are completed on first [render] SSE line', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'analyze' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByTestId('phase-node-analyze').getAttribute('data-phase-state')).toBe('active'),
    )

    // Emit a render SSE tag — timeline render node should become active
    act(() => {
      MockEventSource.lastInstance?.emit('[render] rendering document...')
    })

    await waitFor(() => {
      expect(screen.getByTestId('phase-node-render').getAttribute('data-phase-state')).toBe('active')
    })

    // The three preceding nodes must be completed
    for (const phase of ['plan', 'gather', 'analyze']) {
      expect(
        screen.getByTestId(`phase-node-${phase}`).getAttribute('data-phase-state'),
      ).toBe('completed')
    }
  })

  it('AC2: render-card with "Rendering DOCX…" substep is visible after the first [render] SSE line', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'render' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    act(() => {
      MockEventSource.lastInstance?.emit('[render] rendering document...')
    })

    await waitFor(() => {
      expect(screen.getByTestId('render-card')).toBeInTheDocument()
    })

    // Default sub-step is "Rendering DOCX…"
    expect(screen.getByTestId('render-substep').textContent).toContain('Rendering DOCX')
  })

  it('AC2: render-substep switches to "Converting to PDF…" when a pdf/convert SSE line arrives', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeRunningJob({ current_phase: 'render' }),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    act(() => {
      MockEventSource.lastInstance?.emit('[render] rendering document...')
    })

    await waitFor(() => screen.getByTestId('render-card'))

    act(() => {
      MockEventSource.lastInstance?.emit('[render] saving to PDF via Word...')
    })

    await waitFor(() => {
      expect(screen.getByTestId('render-substep').textContent).toContain('Converting to PDF')
    })
  })

  it('AC3: render-done indicator appears and substep is gone when job transitions from running to done', async () => {
    vi.useFakeTimers()

    let fetchCount = 0
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async () => {
        fetchCount++
        if (fetchCount === 1) {
          return { ok: true, json: async () => makeRunningJob({ current_phase: 'render' }) }
        }
        return { ok: true, json: async () => makeDoneJob() }
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    // Flush the initial poll microtask
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    // Job is running — emit [render] SSE to set renderPhaseState='running'
    act(() => {
      MockEventSource.lastInstance?.emit('[render] rendering...')
    })

    // Advance 2001ms to fire the 2-second re-poll (returns done job)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2001)
    })

    vi.useRealTimers()

    await waitFor(
      () => expect(screen.getByTestId('render-done')).toBeInTheDocument(),
      { timeout: 5000 },
    )

    expect(screen.queryByTestId('render-substep')).not.toBeInTheDocument()
  })

  it('AC4: all four Phase Timeline nodes show as completed when job status is done', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => makeDoneJob(),
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    await waitFor(() => screen.getByTestId('results-view'))

    for (const phase of ['plan', 'gather', 'analyze', 'render']) {
      expect(
        screen.getByTestId(`phase-node-${phase}`).getAttribute('data-phase-state'),
      ).toBe('completed')
    }
  })

  it('AC5: render-error indicator is shown and render-done is absent when job fails during render phase', async () => {
    vi.useFakeTimers()

    let fetchCount = 0
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async () => {
        fetchCount++
        if (fetchCount === 1) {
          return { ok: true, json: async () => makeRunningJob({ current_phase: 'render' }) }
        }
        return {
          ok: true,
          json: async () => ({
            ...makeRunningJob({ current_phase: 'render' }),
            status: 'failed' as const,
            error_message: 'render failed',
          }),
        }
      }),
    )

    render(<JobView jobId="test-job" onBack={vi.fn()} />)

    // Flush the initial poll microtask
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    // Emit [render] SSE to set ssePhaseIdx=3 and renderPhaseState='running'
    act(() => {
      MockEventSource.lastInstance?.emit('[render] rendering...')
    })

    // Advance 2001ms to fire the re-poll (returns failed job)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2001)
    })

    vi.useRealTimers()

    await waitFor(
      () => expect(screen.getByTestId('render-error')).toBeInTheDocument(),
      { timeout: 5000 },
    )

    expect(screen.queryByTestId('render-done')).not.toBeInTheDocument()
  })
})
