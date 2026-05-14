import { useCallback, useEffect, useState } from 'react'
import History from './History'
import JobView from './JobView'
import NewJobForm from './NewJobForm'
import Settings from './Settings'
import SignIn from './SignIn'
import TopNav from './TopNav'

type AuthState = 'loading' | 'authenticated' | 'unauthenticated'

function pushNavigate(path: string) {
  window.history.pushState(null, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export default function App() {
  const [authState, setAuthState] = useState<AuthState>('loading')
  const [path, setPath] = useState(() => window.location.pathname)

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    // Use /api/auth/status instead of /api/jobs so the initial auth check
    // never produces a 401 console error — it always returns 200 with a body.
    fetch('/api/auth/status', { credentials: 'include' })
      .then((r) => r.json())
      .then((data: { authenticated: boolean }) => {
        setAuthState(data.authenticated ? 'authenticated' : 'unauthenticated')
      })
      .catch(() => setAuthState('unauthenticated'))
  }, [])

  const navigate = useCallback((p: string) => pushNavigate(p), [])
  const handleSignOut = useCallback(() => {
    setAuthState('unauthenticated')
    pushNavigate('/')
  }, [])

  if (authState === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950 text-gray-400">
        Loading…
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <SignIn onAuthenticated={() => setAuthState('authenticated')} />
  }

  let view
  if (path === '/settings') {
    view = <Settings onBack={() => navigate('/')} />
  } else if (path === '/history') {
    view = (
      <History
        onSelectJob={(id) => navigate(`/jobs/${id}`)}
        onBack={() => navigate('/')}
      />
    )
  } else if (path.startsWith('/jobs/')) {
    const jobId = path.slice('/jobs/'.length)
    view = <JobView jobId={jobId} onBack={() => navigate('/')} />
  } else {
    view = <NewJobForm onJobCreated={(id) => navigate(`/jobs/${id}`)} />
  }

  return (
    <div className="min-h-screen bg-gray-950">
      <TopNav
        currentPath={path}
        onNavigate={navigate}
        onSignOut={handleSignOut}
      />
      {view}
    </div>
  )
}
