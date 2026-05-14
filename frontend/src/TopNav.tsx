import { useEffect, useState } from 'react'

interface Props {
  currentPath: string
  onNavigate: (path: string) => void
  onSignOut: () => void
}

interface NavLink {
  label: string
  path: string
  match: (p: string) => boolean
}

const LINKS: NavLink[] = [
  { label: 'New Job', path: '/', match: (p) => p === '/' || p.startsWith('/jobs/') },
  { label: 'History', path: '/history', match: (p) => p === '/history' },
  { label: 'Settings', path: '/settings', match: (p) => p === '/settings' },
]

export default function TopNav({ currentPath, onNavigate, onSignOut }: Props) {
  const [signingOut, setSigningOut] = useState(false)
  // Settings indicator — pulse a dot next to "Settings" when not all required keys configured.
  const [keysOk, setKeysOk] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/settings/keys', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: Record<string, boolean> | null) => {
        if (cancelled || !data) return
        // Required: anthropic, perplexity, xai. FMP optional.
        const required = ['anthropic', 'perplexity', 'xai']
        setKeysOk(required.every((p) => !!data[p]))
      })
      .catch(() => {
        if (!cancelled) setKeysOk(null)
      })
    return () => {
      cancelled = true
    }
  }, [currentPath])

  async function handleSignOut() {
    setSigningOut(true)
    try {
      await fetch('/auth/logout', { method: 'POST', credentials: 'include' })
    } catch {
      // ignore — still signal sign-out so the UI returns to the login screen
    }
    onSignOut()
  }

  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        <div className="flex items-center gap-8">
          <div className="flex items-center gap-2">
            <div className="h-2.5 w-2.5 rounded-full bg-blue-500" />
            <span className="font-semibold text-white">SA Runner</span>
          </div>
          <nav className="flex gap-1">
            {LINKS.map((link) => {
              const active = link.match(currentPath)
              const isSettings = link.path === '/settings'
              return (
                <button
                  key={link.path}
                  onClick={() => onNavigate(link.path)}
                  className={`relative rounded px-3 py-1.5 text-sm transition-colors ${
                    active
                      ? 'bg-gray-800 text-white'
                      : 'text-gray-400 hover:bg-gray-800/50 hover:text-gray-200'
                  }`}
                  data-testid={`nav-${link.label.toLowerCase().replace(' ', '-')}`}
                >
                  {link.label}
                  {isSettings && keysOk === false && (
                    <span
                      className="absolute right-0 top-0.5 h-2 w-2 -translate-y-0.5 translate-x-0.5 rounded-full bg-yellow-400"
                      title="Some API keys are not configured"
                    />
                  )}
                </button>
              )
            })}
          </nav>
        </div>
        <button
          onClick={handleSignOut}
          disabled={signingOut}
          className="rounded px-3 py-1.5 text-sm text-gray-400 hover:bg-gray-800/50 hover:text-gray-200 disabled:opacity-50"
          data-testid="nav-sign-out"
        >
          {signingOut ? 'Signing out…' : 'Sign out'}
        </button>
      </div>
    </header>
  )
}
