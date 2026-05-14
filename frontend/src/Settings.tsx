import { useEffect, useState } from 'react'

const PROVIDERS = ['anthropic', 'perplexity', 'xai', 'fmp'] as const
type Provider = (typeof PROVIDERS)[number]

type KeyStatuses = Record<Provider, 'configured' | 'not configured'>

interface Props {
  onBack: () => void
}

export default function Settings({}: Props) {
  const [statuses, setStatuses] = useState<KeyStatuses | null>(null)
  const [rotating, setRotating] = useState<Provider | null>(null)
  const [rotateValue, setRotateValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  useEffect(() => {
    loadStatuses()
  }, [])

  async function loadStatuses() {
    try {
      const resp = await fetch('/api/settings/keys', { credentials: 'include' })
      if (resp.ok) setStatuses(await resp.json())
    } catch {
      // silently ignore network errors on load
    }
  }

  function startRotate(provider: Provider) {
    setRotating(provider)
    setRotateValue('')
    setMessage(null)
  }

  function cancelRotate() {
    setRotating(null)
    setRotateValue('')
  }

  async function saveKey(provider: Provider) {
    const trimmed = rotateValue.trim()
    if (!trimmed) return
    setSaving(true)
    setMessage(null)
    try {
      const resp = await fetch(`/api/settings/keys/${provider}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ value: trimmed }),
      })
      if (resp.ok) {
        setMessage({ kind: 'ok', text: `${provider} key saved.` })
        setRotating(null)
        setRotateValue('')
        await loadStatuses()
      } else {
        const err = await resp.json()
        setMessage({ kind: 'err', text: err.detail ?? 'Failed to save.' })
      }
    } catch {
      setMessage({ kind: 'err', text: 'Network error — could not reach server.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-gray-950 p-8 text-white">
      <div className="mx-auto max-w-xl">
        <h1 className="mb-6 text-2xl font-semibold">Settings</h1>

        <h2 className="mb-4 text-sm font-medium uppercase tracking-wider text-gray-500">
          API Keys
        </h2>

        {message && (
          <p
            className={`mb-4 rounded px-3 py-2 text-sm ${
              message.kind === 'ok'
                ? 'bg-green-900/40 text-green-300'
                : 'bg-red-900/40 text-red-300'
            }`}
          >
            {message.text}
          </p>
        )}

        <div className="space-y-3">
          {PROVIDERS.map((provider) => {
            const status = statuses ? statuses[provider] : null
            const isRotating = rotating === provider
            return (
              <div
                key={provider}
                data-testid={`provider-card-${provider}`}
                className="rounded-lg bg-gray-900 p-4"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium capitalize">{provider}</p>
                    <p
                      className={`text-sm ${
                        status === 'configured' ? 'text-green-400' : 'text-gray-500'
                      }`}
                    >
                      {status ?? '…'}
                    </p>
                  </div>
                  <button
                    onClick={() => (isRotating ? cancelRotate() : startRotate(provider))}
                    className="rounded bg-gray-700 px-3 py-1 text-sm hover:bg-gray-600"
                  >
                    {isRotating ? 'Cancel' : 'Rotate'}
                  </button>
                </div>

                {isRotating && (
                  <div className="mt-3 flex gap-2">
                    <input
                      type="password"
                      value={rotateValue}
                      onChange={(e) => setRotateValue(e.target.value)}
                      placeholder="Paste new API key"
                      autoFocus
                      className="flex-1 rounded border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
                    />
                    <button
                      onClick={() => saveKey(provider)}
                      disabled={saving || !rotateValue.trim()}
                      className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
                    >
                      {saving ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
