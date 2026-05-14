import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Settings from '../Settings'

const ALL_NOT_CONFIGURED = {
  anthropic: 'not configured',
  perplexity: 'not configured',
  xai: 'not configured',
  fmp: 'not configured',
} as const

describe('Settings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders all four provider rows', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ALL_NOT_CONFIGURED,
      }),
    )
    render(<Settings onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/anthropic/i)).toBeInTheDocument()
      expect(screen.getByText(/perplexity/i)).toBeInTheDocument()
      expect(screen.getByText(/xai/i)).toBeInTheDocument()
      expect(screen.getByText(/fmp/i)).toBeInTheDocument()
    })
  })

  it('shows not configured status on initial load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ALL_NOT_CONFIGURED,
      }),
    )
    render(<Settings onBack={vi.fn()} />)

    const badges = await screen.findAllByText('not configured')
    expect(badges).toHaveLength(4)
  })

  it('calls onBack when Back button is clicked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ALL_NOT_CONFIGURED,
      }),
    )
    const onBack = vi.fn()
    render(<Settings onBack={onBack} />)

    await screen.findAllByText('not configured')
    await userEvent.click(screen.getByRole('button', { name: /back/i }))

    expect(onBack).toHaveBeenCalledOnce()
  })

  it('shows key input after clicking Rotate', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ALL_NOT_CONFIGURED,
      }),
    )
    render(<Settings onBack={vi.fn()} />)

    await screen.findAllByText('not configured')
    const rotateButtons = screen.getAllByRole('button', { name: /rotate/i })
    await userEvent.click(rotateButtons[0])

    expect(
      screen.getByPlaceholderText(/paste new api key/i),
    ).toBeInTheDocument()
  })

  it('shows configured status after saving a key successfully', async () => {
    const fetchMock = vi
      .fn()
      // initial load
      .mockResolvedValueOnce({ ok: true, json: async () => ALL_NOT_CONFIGURED })
      // PUT request
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) })
      // reload after save
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...ALL_NOT_CONFIGURED,
          anthropic: 'configured',
        }),
      })
    vi.stubGlobal('fetch', fetchMock)

    render(<Settings onBack={vi.fn()} />)
    await screen.findAllByText('not configured')

    const rotateButtons = screen.getAllByRole('button', { name: /rotate/i })
    await userEvent.click(rotateButtons[0]) // anthropic (first row)

    await userEvent.type(
      screen.getByPlaceholderText(/paste new api key/i),
      'sk-ant-test-12345',
    )
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() =>
      expect(screen.getByText('configured')).toBeInTheDocument(),
    )
    // Success message
    expect(screen.getByText(/key saved/i)).toBeInTheDocument()
  })

  it('shows error message on save failure', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ALL_NOT_CONFIGURED })
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: 'Failed to save.' }),
      })
    vi.stubGlobal('fetch', fetchMock)

    render(<Settings onBack={vi.fn()} />)
    await screen.findAllByText('not configured')

    const rotateButtons = screen.getAllByRole('button', { name: /rotate/i })
    await userEvent.click(rotateButtons[0])

    await userEvent.type(
      screen.getByPlaceholderText(/paste new api key/i),
      'bad-key',
    )
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() =>
      expect(screen.getByText(/failed to save/i)).toBeInTheDocument(),
    )
  })
})
