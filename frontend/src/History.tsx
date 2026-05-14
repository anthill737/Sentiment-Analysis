import { useEffect, useState } from 'react'

interface JobSummary {
  id: string
  subject: string
  status: 'running' | 'done' | 'failed' | 'cancelled' | 'awaiting_clarification'
  verdict: string | null
  score: number | null
  error_message: string | null
  degraded_sources: string[]
  created_at: string | null
  completed_at: string | null
  total_cost_usd: number | null
  total_duration_seconds: number | null
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const mins = Math.floor(s / 60)
  const rem = s % 60
  if (mins < 60) return `${mins}m ${rem}s`
  const hrs = Math.floor(mins / 60)
  const minRem = mins % 60
  return `${hrs}h ${minRem}m`
}

function formatUsd(usd: number | null | undefined): string {
  if (usd == null) return '—'
  if (usd < 0.01) return '<$0.01'
  return `$${usd.toFixed(2)}`
}

interface Props {
  onSelectJob: (id: string) => void
  onBack: () => void
}

function verdictColorClass(verdict: string): string {
  const v = verdict.toLowerCase()
  if (v.includes('pursue') || v.includes('promising')) return 'bg-green-500/20 text-green-300'
  if (v.includes('explore') || v.includes('worth')) return 'bg-blue-500/20 text-blue-300'
  if (v.includes('skip') || v.includes('pass') || v.includes('avoid')) return 'bg-red-500/20 text-red-300'
  return 'bg-gray-500/20 text-gray-300'
}

function VerdictPill({ verdict, score }: { verdict: string; score: number | null }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${verdictColorClass(verdict)}`}
    >
      {verdict}
      {score != null && <span className="opacity-75">· {score}/10</span>}
    </span>
  )
}

export default function History({ onSelectJob }: Props) {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [resynthesizingId, setResynthesizingId] = useState<string | null>(null)
  const [resynthError, setResynthError] = useState<string | null>(null)

  async function handleResynthesize(jobId: string) {
    if (resynthesizingId) return
    if (!window.confirm(
      'Re-generate the report for this job? It will re-run the analysis and ' +
      'rendering on the existing fetched data (~$2-5 in Claude calls).'
    )) return
    setResynthesizingId(jobId)
    setResynthError(null)
    try {
      const resp = await fetch(`/api/jobs/${jobId}/resynthesize`, {
        method: 'POST',
        credentials: 'include',
      })
      if (resp.ok) {
        onSelectJob(jobId)  // jump to job view so user can watch progress
      } else {
        const data = await resp.json().catch(() => null)
        setResynthError(data?.detail ?? 'Re-generate failed.')
      }
    } catch {
      setResynthError('Could not reach the server.')
    } finally {
      setResynthesizingId(null)
    }
  }

  useEffect(() => {
    fetch('/api/jobs', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        setJobs(Array.isArray(data) ? data : [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  return (
    <div className="bg-gray-950 p-8 text-white">
      <h1 className="text-2xl font-semibold mb-6">History</h1>

      {resynthError && (
        <div className="mb-4 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {resynthError}
        </div>
      )}

      {loading && <p className="text-gray-400">Loading…</p>}

      {!loading && jobs.length === 0 && (
        <p className="text-gray-500">No jobs yet.</p>
      )}

      <div className="space-y-4">
        {jobs.map((job) => (
          <div
            key={job.id}
            className="rounded-lg border border-gray-800 bg-gray-900 p-4 cursor-pointer hover:border-gray-700 transition-colors"
            onClick={() => onSelectJob(job.id)}
            data-testid={`history-card-${job.id}`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <p className="font-medium truncate">{job.subject}</p>

                {/* Timestamps */}
                <p
                  className="mt-1 text-xs text-gray-500"
                  data-testid={`card-created-at-${job.id}`}
                >
                  Created:{' '}
                  {job.created_at
                    ? new Date(job.created_at).toLocaleString()
                    : '—'}
                </p>
                {job.status === 'done' && job.completed_at && (
                  <p
                    className="text-xs text-gray-500"
                    data-testid={`card-completed-at-${job.id}`}
                  >
                    Completed:{' '}
                    {new Date(job.completed_at).toLocaleString()}
                  </p>
                )}
              </div>
              <StatusBadge status={job.status} />
            </div>

            {/* Verdict Pill + run stats */}
            {job.status === 'done' && job.verdict && (
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <VerdictPill verdict={job.verdict} score={job.score} />
                {(job.total_duration_seconds != null || job.total_cost_usd != null) && (
                  <span className="text-xs text-gray-400">
                    {job.total_duration_seconds != null && (
                      <>⏱ {formatDuration(job.total_duration_seconds)}</>
                    )}
                    {job.total_duration_seconds != null && job.total_cost_usd != null && (
                      <span className="mx-1.5">·</span>
                    )}
                    {job.total_cost_usd != null && (
                      <>💵 {formatUsd(job.total_cost_usd)}</>
                    )}
                  </span>
                )}
              </div>
            )}

            {/* Degraded coverage */}
            {job.degraded_sources.length > 0 && (
              <div
                className="mt-3 rounded border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-300"
                data-testid="degraded-coverage-banner"
              >
                <span className="font-semibold">Degraded coverage</span>
                {' — '}
                {job.degraded_sources.join(', ')} failed
              </div>
            )}

            {/* Download links + Re-generate for completed jobs */}
            {job.status === 'done' && (
              <div className="mt-3 flex items-center gap-3">
                <a
                  href={`/api/jobs/${job.id}/download/pdf`}
                  download
                  data-testid={`history-download-pdf-${job.id}`}
                  className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  Download PDF
                </a>
                <span className="text-gray-700">·</span>
                <a
                  href={`/api/jobs/${job.id}/download/docx`}
                  download
                  data-testid={`history-download-docx-${job.id}`}
                  className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  Download DOCX
                </a>
                <span className="text-gray-700">·</span>
                <button
                  onClick={(e) => { e.stopPropagation(); handleResynthesize(job.id) }}
                  disabled={resynthesizingId === job.id}
                  className="text-xs text-indigo-300 hover:text-indigo-200 transition-colors disabled:opacity-50"
                  data-testid={`history-resynth-${job.id}`}
                >
                  {resynthesizingId === job.id ? 'Starting…' : 'Re-generate'}
                </button>
              </div>
            )}

            {/* Re-generate for failed / cancelled — same checkpoint data is reusable */}
            {(job.status === 'failed' || job.status === 'cancelled') && (
              <div className="mt-3">
                <button
                  onClick={(e) => { e.stopPropagation(); handleResynthesize(job.id) }}
                  disabled={resynthesizingId === job.id}
                  className="text-xs text-indigo-300 hover:text-indigo-200 transition-colors disabled:opacity-50"
                  data-testid={`history-resynth-${job.id}`}
                >
                  {resynthesizingId === job.id ? 'Starting…' : 'Re-generate report'}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'running') {
    return (
      <span className="shrink-0 rounded-full bg-blue-600/20 px-2.5 py-0.5 text-xs font-medium text-blue-300">
        Running
      </span>
    )
  }
  if (status === 'done') {
    return (
      <span className="shrink-0 rounded-full bg-green-600/20 px-2.5 py-0.5 text-xs font-medium text-green-300">
        Done
      </span>
    )
  }
  if (status === 'cancelled') {
    return (
      <span className="shrink-0 rounded-full bg-gray-600/20 px-2.5 py-0.5 text-xs font-medium text-gray-300">
        Cancelled
      </span>
    )
  }
  if (status === 'awaiting_clarification') {
    return (
      <span className="shrink-0 rounded-full bg-blue-600/20 px-2.5 py-0.5 text-xs font-medium text-blue-300">
        Awaiting input
      </span>
    )
  }
  return (
    <span className="shrink-0 rounded-full bg-red-600/20 px-2.5 py-0.5 text-xs font-medium text-red-300">
      Failed
    </span>
  )
}
