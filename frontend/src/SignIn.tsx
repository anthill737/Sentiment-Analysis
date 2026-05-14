import { type FormEvent, useState } from 'react'

interface Props {
  onAuthenticated: () => void
}

export default function SignIn({ onAuthenticated }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!password) {
      setError('Password is required.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const resp = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (resp.ok) {
        onAuthenticated()
      } else {
        setError('Invalid password. Please try again.')
      }
    } catch {
      setError('Could not connect to the server.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl bg-gray-900 p-8 shadow-lg"
      >
        <h1 className="mb-6 text-center text-2xl font-semibold text-white">
          SA Runner
        </h1>
        {error && (
          <p className="mb-4 rounded bg-red-900/40 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}
        <label className="mb-1 block text-sm text-gray-400" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
          placeholder="Enter your password"
          autoFocus
        />
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-indigo-600 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? 'Signing in…' : 'Sign In'}
        </button>
      </form>
    </div>
  )
}
