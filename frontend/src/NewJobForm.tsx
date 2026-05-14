import { type FormEvent, useEffect, useState } from 'react'

interface Props {
  onJobCreated: (jobId: string) => void
}

interface FormState {
  subject: string
  time_window: string
  focus_areas: string
  include_public_market: boolean
  tickers: string
  include_github: boolean
  github_code_search: boolean
  include_steam: boolean
}

const REQUIRED_KEYS = ['anthropic', 'perplexity', 'xai'] as const
const KEY_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  perplexity: 'Perplexity',
  xai: 'xAI',
  fmp: 'FMP',
  github: 'GitHub',
}

function navigateTo(path: string) {
  window.history.pushState(null, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export default function NewJobForm({ onJobCreated }: Props) {
  const [form, setForm] = useState<FormState>({
    subject: '',
    time_window: '1y',
    focus_areas: '',
    include_public_market: false,
    tickers: '',
    include_github: false,
    github_code_search: false,
    include_steam: false,
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [missingKeys, setMissingKeys] = useState<string[] | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/settings/keys', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: Record<string, boolean> | null) => {
        if (cancelled) return
        if (!data) {
          setMissingKeys([])
          return
        }
        const needed = REQUIRED_KEYS.filter((k) => !data[k])
        if (form.include_public_market && !data.fmp) needed.push('fmp')
        if (form.include_github && form.github_code_search && !data.github) needed.push('github')
        setMissingKeys(needed)
      })
      .catch(() => {
        if (!cancelled) setMissingKeys([])
      })
    return () => {
      cancelled = true
    }
    // Re-check when the user toggles any opt-in fetcher.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.include_public_market, form.include_github, form.github_code_search])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (missingKeys && missingKeys.length > 0) return
    setLoading(true)
    setError('')
    try {
      const resp = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(form),
      })
      if (resp.ok) {
        const { id } = await resp.json()
        onJobCreated(id)
      } else {
        const data = await resp.json().catch(() => null)
        setError(data?.detail ?? 'Failed to start job. Please try again.')
      }
    } catch {
      setError('Could not connect to the server.')
    } finally {
      setLoading(false)
    }
  }

  const blocked = missingKeys !== null && missingKeys.length > 0

  return (
    <div className="flex items-center justify-center bg-gray-950 p-4 pt-12">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-lg rounded-xl bg-gray-900 p-8 shadow-lg"
      >
        <h1 className="mb-6 text-2xl font-semibold text-white">New Research Job</h1>

        {blocked && (
          <div
            className="mb-4 rounded border border-yellow-500/40 bg-yellow-500/10 px-3 py-2.5 text-sm text-yellow-200"
            data-testid="missing-keys-banner"
          >
            <p className="mb-1 font-semibold">Configure API keys before starting a job</p>
            <p className="text-yellow-200/80">
              Missing: {missingKeys!.map((k) => KEY_LABELS[k] ?? k).join(', ')}.{' '}
              <button
                type="button"
                onClick={() => navigateTo('/settings')}
                className="underline hover:text-yellow-100"
              >
                Open Settings
              </button>
            </p>
          </div>
        )}

        {error && (
          <p className="mb-4 rounded bg-red-900/40 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}

        <div className="mb-4">
          <label className="mb-1 block text-sm text-gray-400" htmlFor="subject">
            Subject *
          </label>
          <textarea
            id="subject"
            rows={3}
            required
            value={form.subject}
            onChange={(e) => setForm({ ...form, subject: e.target.value })}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="What are you researching?"
          />
        </div>

        <div className="mb-4">
          <label className="mb-1 block text-sm text-gray-400" htmlFor="time_window">
            Time Window
          </label>
          <select
            id="time_window"
            value={form.time_window}
            onChange={(e) => setForm({ ...form, time_window: e.target.value })}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-white focus:border-indigo-500 focus:outline-none"
          >
            <option value="30d">30 days</option>
            <option value="90d">90 days</option>
            <option value="180d">180 days</option>
            <option value="1y">1 year</option>
            <option value="2y">2 years</option>
            <option value="5y">5 years</option>
            <option value="all">All time</option>
          </select>
        </div>

        <div className="mb-4">
          <label className="mb-1 block text-sm text-gray-400" htmlFor="focus_areas">
            Focus Areas
          </label>
          <input
            id="focus_areas"
            type="text"
            value={form.focus_areas}
            onChange={(e) => setForm({ ...form, focus_areas: e.target.value })}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="e.g. pricing, competition, timing"
          />
        </div>

        <div className="mb-4 flex items-center gap-2">
          <input
            id="include_public_market"
            type="checkbox"
            checked={form.include_public_market}
            onChange={(e) =>
              setForm({
                ...form,
                include_public_market: e.target.checked,
                tickers: e.target.checked ? form.tickers : '',
              })
            }
            className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-600"
          />
          <label htmlFor="include_public_market" className="text-sm text-gray-400">
            Include public market data
          </label>
        </div>

        {form.include_public_market && (
          <div className="mb-4">
            <label className="mb-1 block text-sm text-gray-400" htmlFor="tickers">
              Tickers
            </label>
            <input
              id="tickers"
              type="text"
              value={form.tickers}
              onChange={(e) => setForm({ ...form, tickers: e.target.value })}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
              placeholder="e.g. AAPL, MSFT, GOOG"
            />
          </div>
        )}

        <div className="mb-4 flex items-center gap-2">
          <input
            id="include_github"
            type="checkbox"
            checked={form.include_github}
            onChange={(e) =>
              setForm({
                ...form,
                include_github: e.target.checked,
                github_code_search: e.target.checked ? form.github_code_search : false,
              })
            }
            className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-600"
          />
          <label htmlFor="include_github" className="text-sm text-gray-400">
            Include GitHub repositories
          </label>
        </div>

        {form.include_github && (
          <div className="mb-4 ml-6 flex items-center gap-2">
            <input
              id="github_code_search"
              type="checkbox"
              checked={form.github_code_search}
              onChange={(e) =>
                setForm({ ...form, github_code_search: e.target.checked })
              }
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-600"
            />
            <label htmlFor="github_code_search" className="text-sm text-gray-400">
              Also search code (requires GITHUB_TOKEN in API Keys settings)
            </label>
          </div>
        )}

        <div className="mb-4 flex items-center gap-2">
          <input
            id="include_steam"
            type="checkbox"
            checked={form.include_steam}
            onChange={(e) => setForm({ ...form, include_steam: e.target.checked })}
            className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-600"
          />
          <label htmlFor="include_steam" className="text-sm text-gray-400">
            Include Steam games & reviews (only useful for video-game ideas)
          </label>
        </div>

        <button
          type="submit"
          disabled={loading || blocked}
          className="w-full rounded-lg bg-indigo-600 py-2 font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? 'Starting…' : blocked ? 'Configure API keys to continue' : 'Start Research'}
        </button>
      </form>
    </div>
  )
}
