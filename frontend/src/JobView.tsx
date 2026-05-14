import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface JobData {
  id: string
  subject: string
  status: 'running' | 'done' | 'failed' | 'cancelled' | 'awaiting_clarification'
  current_phase: string | null
  verdict: string | null
  score: number | null
  error_message: string | null
  degraded_sources: string[]
  clarification_questions: string[]
  created_at: string | null
  completed_at: string | null
  gather_status?: Record<string, GatherSourceStatus>
  // Results View fields (populated by the API when status === 'done')
  angle_scores?: Record<string, number>
  pursue_reasons?: string[]
  pass_reasons?: string[]
  confidence?: number | null
  total_cost_usd?: number | null
  total_duration_seconds?: number | null
  cost_breakdown?: {
    total_usd: number
    total_input_tokens: number
    total_output_tokens: number
    total_calls: number
    by_provider: Record<string, {usd: number; input_tokens: number; output_tokens: number; calls: number}>
  } | null
}

interface GatherSourceStatus {
  step: number
  total: number
  angle: string
}

interface SectionsProgress {
  current: number
  total: number
  title: string
}

interface Props {
  jobId: string
  onBack: () => void
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PHASES = ['plan', 'gather', 'analyze', 'render'] as const
type Phase = (typeof PHASES)[number]

const PHASE_LABELS: Record<Phase, string> = {
  plan: 'Plan',
  gather: 'Gather',
  analyze: 'Analyze',
  render: 'Render',
}

const GATHER_SOURCES = ['perplexity', 'xai', 'trends', 'fmp'] as const
type GatherSource = (typeof GATHER_SOURCES)[number]

const SOURCE_LABELS: Record<GatherSource, string> = {
  perplexity: 'Perplexity',
  xai: 'xAI',
  trends: 'Trends',
  fmp: 'FMP',
}

const ANALYZE_STEPS = ['extract', 'cluster', 'score', 'sections', 'executive', 'charts', 'assemble'] as const
type AnalyzeStep = (typeof ANALYZE_STEPS)[number]

const ANALYZE_STEP_LABELS: Record<AnalyzeStep, string> = {
  extract: 'Extract Evidence',
  cluster: 'Cluster Themes',
  score: 'Score Angles',
  sections: 'Write Sections',
  executive: 'Write Executive',
  charts: 'Generate Charts',
  assemble: 'Assemble Report',
}

// Source tag → Tailwind text-color class for Log Panel lines
const TAG_COLORS: Record<string, string> = {
  plan: 'text-gray-300',
  perplexity: 'text-blue-400',
  xai: 'text-purple-400',
  trends: 'text-green-400',
  fmp: 'text-yellow-400',
  extract: 'text-orange-300',
  cluster: 'text-orange-300',
  score: 'text-orange-300',
  sections: 'text-orange-300',
  executive: 'text-orange-300',
  charts: 'text-orange-300',
  assemble: 'text-orange-300',
  render: 'text-pink-400',
  gather: 'text-yellow-500',
}

const PROGRESS_RE = /^\[(perplexity|xai|trends|fmp)\] \[(\d+)\/(\d+)\] angle=(.+)$/
// Parses: "[gather] degraded coverage: src1, src2 failed"
const DEGRADED_COVERAGE_RE = /^\[gather\] degraded coverage: (.+) failed$/
// Parses: "[sections] [N/M] writing '<title>'"
const SECTIONS_PROGRESS_RE = /^\[sections\] \[(\d+)\/(\d+)\] writing '(.+)'$/
// Matches any analyze-step tag at start of line
const ANALYZE_STEP_RE = /^\[(extract|cluster|score|sections|executive|charts|assemble)\]/
// Detects PDF conversion sub-step in render progress lines (Word convert step).
const RENDER_PDF_RE = /convert|pdf|word/i

// SSE tag → phase index mapping (used to derive phase from log lines in real time)
const _GATHER_TAG_SET = new Set(['perplexity', 'xai', 'trends', 'fmp'])
const _ANALYZE_TAG_SET = new Set([
  'extract', 'cluster', 'score', 'sections', 'executive', 'charts', 'assemble',
])

function sseTagToPhaseIdx(tag: string): number {
  if (tag === 'plan') return 0
  if (_GATHER_TAG_SET.has(tag)) return 1
  if (_ANALYZE_TAG_SET.has(tag)) return 2
  if (tag === 'render') return 3
  return -1
}

// Angle display order for the score chart
const ANGLE_ORDER = ['demand', 'competition', 'pricing', 'niches', 'timing', 'features'] as const

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function phaseIndex(phase: string | null): number {
  if (!phase) return -1
  return PHASES.indexOf(phase as Phase)
}

function getTagColor(line: string): string {
  const m = line.match(/^\[([^\]]+)\]/)
  if (m) return TAG_COLORS[m[1]] ?? 'text-gray-400'
  return 'text-gray-400'
}

function getLineSource(line: string): string {
  const m = line.match(/^\[([^\]]+)\]/)
  return m ? m[1] : ''
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function verdictColorClass(verdict: string): string {
  const v = verdict.toLowerCase()
  if (v.includes('pursue') || v.includes('promising')) return 'bg-green-500/20 text-green-300'
  if (v.includes('explore') || v.includes('worth')) return 'bg-blue-500/20 text-blue-300'
  if (v.includes('skip') || v.includes('pass') || v.includes('avoid')) return 'bg-red-500/20 text-red-300'
  return 'bg-gray-500/20 text-gray-300'
}

// ---------------------------------------------------------------------------
// VerdictPill
// ---------------------------------------------------------------------------

function VerdictPill({ verdict, score }: { verdict: string; score: number | null }) {
  return (
    <span
      data-testid="verdict-pill"
      className={`inline-flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-semibold ${verdictColorClass(verdict)}`}
    >
      {verdict}
      {score != null && <span className="opacity-75">· {score}/10</span>}
    </span>
  )
}

// ---------------------------------------------------------------------------
// RunStats — total cost + duration with hoverable per-provider breakdown
// ---------------------------------------------------------------------------

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
  if (usd < 10) return `$${usd.toFixed(2)}`
  return `$${usd.toFixed(2)}`
}

function RunStats({
  durationSec,
  costUsd,
  breakdown,
}: {
  durationSec: number | null | undefined
  costUsd: number | null | undefined
  breakdown:
    | {
        by_provider: Record<
          string,
          { usd: number; input_tokens: number; output_tokens: number; calls: number }
        >
      }
    | null
    | undefined
}) {
  if (durationSec == null && costUsd == null) return null
  const providers = breakdown?.by_provider || {}
  const providerEntries = Object.entries(providers).sort(
    ([, a], [, b]) => b.usd - a.usd,
  )
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-gray-400">
      {durationSec != null && (
        <span title="Wall-clock time from job creation to completion">
          ⏱ {formatDuration(durationSec)}
        </span>
      )}
      {costUsd != null && (
        <span
          className="group relative cursor-help"
          title="Estimated cost based on API token usage (see breakdown below)"
        >
          💵 {formatUsd(costUsd)}
          {providerEntries.length > 0 && (
            <div
              className="invisible absolute left-0 top-full z-10 mt-1 min-w-[260px] rounded-lg border border-gray-700 bg-gray-900 p-3 text-xs shadow-lg group-hover:visible"
            >
              <div className="mb-1.5 font-semibold text-gray-200">Cost breakdown</div>
              {providerEntries.map(([provider, b]) => (
                <div key={provider} className="flex justify-between gap-3 py-0.5">
                  <span className="capitalize text-gray-300">{provider}</span>
                  <span className="text-gray-400">
                    {formatUsd(b.usd)}{' '}
                    <span className="opacity-60">({b.calls} call{b.calls === 1 ? '' : 's'})</span>
                  </span>
                </div>
              ))}
            </div>
          )}
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AngleScoreChart — custom horizontal bar chart (no external dependency)
// ---------------------------------------------------------------------------

function AngleScoreChart({ scores }: { scores: Record<string, number> }) {
  const data = ANGLE_ORDER
    .filter((key) => key in scores)
    .map((key) => ({
      key,
      label: key.charAt(0).toUpperCase() + key.slice(1),
      score: Number(scores[key]),
    }))

  if (data.length === 0) return null

  return (
    <div data-testid="angle-score-chart" className="space-y-2">
      {data.map(({ key, label, score }) => (
        <div key={key} data-testid="score-bar" className="flex items-center gap-3">
          <span className="w-24 shrink-0 text-right text-xs text-gray-400">{label}</span>
          <div className="flex-1 h-4 rounded bg-gray-800 overflow-hidden">
            <div
              className="h-full rounded bg-blue-500 transition-all duration-500"
              style={{ width: `${(score / 10) * 100}%` }}
            />
          </div>
          <span
            className="w-8 shrink-0 text-right text-xs font-mono text-gray-300"
            data-testid={`bar-score-${key}`}
          >
            {score}
          </span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ResultsView — shown when Job status === 'done'
// ---------------------------------------------------------------------------

function ResultsView({ job }: { job: JobData }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="space-y-6"
      data-testid="results-view"
    >
      {/* Verdict pill + download buttons */}
      <div className="flex flex-wrap items-center gap-4">
        {job.verdict && <VerdictPill verdict={job.verdict} score={job.score} />}
        <RunStats
          durationSec={job.total_duration_seconds}
          costUsd={job.total_cost_usd}
          breakdown={job.cost_breakdown}
        />
        <div className="flex flex-wrap gap-2">
          <a
            href={`/api/jobs/${job.id}/download/pdf`}
            download
            data-testid="download-pdf-btn"
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500 transition-colors"
          >
            ↓ Download PDF
          </a>
          <a
            href={`/api/jobs/${job.id}/download/docx`}
            download
            data-testid="download-docx-btn"
            className="inline-flex items-center gap-1.5 rounded-lg bg-gray-700 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-600 transition-colors"
          >
            ↓ Download DOCX
          </a>
        </div>
      </div>

      {/* Angle score chart */}
      {job.angle_scores && Object.keys(job.angle_scores).length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
            Angle Scores
          </h3>
          <AngleScoreChart scores={job.angle_scores} />
        </div>
      )}

      {/* Pursue / pass reason cards */}
      {((job.pursue_reasons && job.pursue_reasons.length > 0) ||
        (job.pass_reasons && job.pass_reasons.length > 0)) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {job.pursue_reasons && job.pursue_reasons.length > 0 && (
            <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-green-400">
                Reasons to Pursue
              </h3>
              <ul className="space-y-2">
                {job.pursue_reasons.map((reason, i) => (
                  <li
                    key={i}
                    data-testid={`pursue-reason-${i}`}
                    className="flex items-start gap-2 text-sm text-gray-200"
                  >
                    <span className="mt-0.5 shrink-0 text-green-500">+</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {job.pass_reasons && job.pass_reasons.length > 0 && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-red-400">
                Reasons to Pass
              </h3>
              <ul className="space-y-2">
                {job.pass_reasons.map((reason, i) => (
                  <li
                    key={i}
                    data-testid={`pass-reason-${i}`}
                    className="flex items-start gap-2 text-sm text-gray-200"
                  >
                    <span className="mt-0.5 shrink-0 text-red-400">−</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// PhaseTimeline
// ---------------------------------------------------------------------------

interface PhaseTimelineProps {
  currentPhase: string | null
  jobStatus: 'running' | 'done' | 'failed' | 'cancelled' | 'awaiting_clarification'
  phaseElapsed: Record<string, number>
}

function PhaseTimeline({ currentPhase, jobStatus, phaseElapsed }: PhaseTimelineProps) {
  const activeIdx = phaseIndex(currentPhase)

  return (
    <div className="flex items-center" data-testid="phase-timeline">
      {PHASES.map((phase, idx) => {
        const isDone = jobStatus === 'done'
        const isCompleted = isDone || (activeIdx >= 0 && idx < activeIdx)
        const isActive = jobStatus === 'running' && idx === activeIdx

        let state: 'active' | 'completed' | 'pending'
        if (isActive) state = 'active'
        else if (isCompleted) state = 'completed'
        else state = 'pending'

        const elapsed = phaseElapsed[phase] ?? 0

        return (
          <div key={phase} className="flex items-center">
            {/* Node */}
            <div
              data-testid={`phase-node-${phase}`}
              data-phase-state={state}
              className="flex flex-col items-center"
            >
              <div
                className={[
                  'relative flex h-10 w-10 items-center justify-center rounded-full border-2 font-semibold text-xs transition-all duration-500',
                  isActive
                    ? 'animate-pulse border-blue-400 bg-blue-500/20 text-blue-300 ring-4 ring-blue-400/40'
                    : isCompleted
                    ? 'border-green-500 bg-green-500/20 text-green-300'
                    : 'border-gray-600 bg-gray-800 text-gray-500',
                ].join(' ')}
              >
                {isCompleted ? (
                  <motion.svg
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: 'spring', stiffness: 300, damping: 20 }}
                    className="h-5 w-5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2.5}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </motion.svg>
                ) : (
                  <span>{idx + 1}</span>
                )}
                {/* Expanding ring for active phase */}
                {isActive && (
                  <motion.div
                    className="absolute inset-0 rounded-full border-2 border-blue-400"
                    animate={{ scale: [1, 1.5, 1], opacity: [0.6, 0, 0.6] }}
                    transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                  />
                )}
              </div>
              <span
                className={[
                  'mt-1.5 text-xs font-medium',
                  isActive
                    ? 'text-blue-300'
                    : isCompleted
                    ? 'text-green-400'
                    : 'text-gray-600',
                ].join(' ')}
              >
                {PHASE_LABELS[phase]}
              </span>
              {elapsed > 0 && (
                <span
                  className="mt-0.5 text-xs text-gray-500"
                  data-testid={`phase-elapsed-${phase}`}
                >
                  {formatElapsed(elapsed)}
                </span>
              )}
            </div>

            {/* Connector between nodes */}
            {idx < PHASES.length - 1 && (
              <div
                className={[
                  'mx-2 h-0.5 w-12 transition-colors duration-500',
                  idx < activeIdx || isDone ? 'bg-green-600' : 'bg-gray-700',
                ].join(' ')}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// GatherCard
// ---------------------------------------------------------------------------

interface GatherCardProps {
  source: GatherSource
  status: GatherSourceStatus | undefined
  /** Overall gather phase elapsed — used as fallback when no per-source timer yet. */
  phaseElapsed: number
  /** Per-source elapsed; frozen when isDone or isError. */
  sourceElapsed?: number
  phaseActive: boolean
  isDone: boolean
  isError: boolean
}

function GatherCard({
  source,
  status,
  phaseElapsed,
  sourceElapsed,
  phaseActive,
  isDone,
  isError,
}: GatherCardProps) {
  const label = SOURCE_LABELS[source]
  const hasData = !!status

  const borderClass = isError
    ? 'border-red-500/50 bg-red-500/5'
    : isDone
    ? 'border-green-500/40 bg-green-500/5'
    : hasData
    ? 'border-blue-500/40 bg-blue-500/5'
    : 'border-gray-700 bg-gray-800/50'

  const displayElapsed = sourceElapsed !== undefined ? sourceElapsed : phaseElapsed

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 260, damping: 22 }}
      data-testid={`gather-card-${source}`}
      className={[
        'relative overflow-hidden rounded-lg border p-4',
        borderClass,
      ].join(' ')}
    >
      {/* Shimmer overlay — only while actively running (not done or errored) */}
      {phaseActive && !isDone && !isError && (
        <motion.div
          className="pointer-events-none absolute inset-0 bg-gradient-to-r from-transparent via-blue-400/5 to-transparent"
          animate={{ x: ['-100%', '100%'] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'linear' }}
        />
      )}

      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-200">{label}</span>
        {(isDone || isError || phaseActive) && displayElapsed > 0 && (
          <span
            className="text-xs text-gray-400"
            data-testid={`gather-elapsed-${source}`}
          >
            {formatElapsed(displayElapsed)}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-400">
        {isError ? (
          <span
            data-testid={`gather-error-${source}`}
            className="flex items-center gap-1 text-red-400"
          >
            <svg
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
            Failed
          </span>
        ) : isDone ? (
          <span
            data-testid={`gather-done-${source}`}
            className="flex items-center gap-1 text-green-400"
          >
            <motion.svg
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 300, damping: 20 }}
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </motion.svg>
            Done
          </span>
        ) : hasData ? (
          <>
            <span
              data-testid={`gather-step-${source}`}
              className="font-mono text-blue-300"
            >
              {status!.step}/{status!.total}
            </span>
            {' — '}
            <span data-testid={`gather-angle-${source}`} className="text-gray-300">
              {status!.angle}
            </span>
          </>
        ) : (
          <span className="animate-pulse text-gray-500">waiting…</span>
        )}
      </div>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// AnalyzeStepCard — one card per Analyze Phase Step
// ---------------------------------------------------------------------------

type AnalyzeStepState = 'running' | 'done' | 'error'

interface AnalyzeStepCardProps {
  step: AnalyzeStep
  stepState: AnalyzeStepState
  elapsed: number
  sectionsProgress: SectionsProgress | null
}

function AnalyzeStepCard({ step, stepState, elapsed, sectionsProgress }: AnalyzeStepCardProps) {
  const label = ANALYZE_STEP_LABELS[step]

  const borderClass =
    stepState === 'error'
      ? 'border-red-500/50 bg-red-500/5'
      : stepState === 'done'
      ? 'border-green-500/40 bg-green-500/5'
      : 'border-orange-500/40 bg-orange-500/5'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 260, damping: 22 }}
      data-testid={`analyze-step-card-${step}`}
      className={[
        'relative overflow-hidden rounded-lg border p-4',
        borderClass,
      ].join(' ')}
    >
      {/* Shimmer — only while running */}
      {stepState === 'running' && (
        <motion.div
          className="pointer-events-none absolute inset-0 bg-gradient-to-r from-transparent via-orange-400/5 to-transparent"
          animate={{ x: ['-100%', '100%'] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'linear' }}
        />
      )}

      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-200">{label}</span>
        {elapsed > 0 && (
          <span
            className="text-xs text-gray-400"
            data-testid={`analyze-elapsed-${step}`}
          >
            {formatElapsed(elapsed)}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1.5 text-xs">
        {stepState === 'error' ? (
          <span
            data-testid={`analyze-error-${step}`}
            className="flex items-center gap-1 text-red-400"
          >
            <svg
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
            Failed
          </span>
        ) : stepState === 'done' ? (
          <span
            data-testid={`analyze-done-${step}`}
            className="flex items-center gap-1 text-green-400"
          >
            <motion.svg
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 300, damping: 20 }}
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </motion.svg>
            Done
          </span>
        ) : step === 'sections' && sectionsProgress ? (
          <span
            data-testid="analyze-sections-progress"
            className="text-orange-300"
          >
            writing &apos;{sectionsProgress.title}&apos; — section {sectionsProgress.current} of{' '}
            {sectionsProgress.total}
          </span>
        ) : (
          <span className="animate-pulse text-gray-500">processing…</span>
        )}
      </div>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// RenderCard — Phase Card for the Render phase (running/done/error states)
// ---------------------------------------------------------------------------

type RenderStepState = 'running' | 'done' | 'error'

interface RenderCardProps {
  state: RenderStepState
  subStep: 'docx' | 'pdf'
  elapsed: number
}

function RenderCard({ state, subStep, elapsed }: RenderCardProps) {
  const subStepLabel = subStep === 'pdf' ? 'Converting to PDF…' : 'Rendering DOCX…'

  const borderClass =
    state === 'error'
      ? 'border-red-500/50 bg-red-500/5'
      : state === 'done'
      ? 'border-green-500/40 bg-green-500/5'
      : 'border-pink-500/40 bg-pink-500/5'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 260, damping: 22 }}
      data-testid="render-card"
      className={[
        'relative overflow-hidden rounded-lg border p-4',
        borderClass,
      ].join(' ')}
    >
      {/* Shimmer — only while running */}
      {state === 'running' && (
        <motion.div
          className="pointer-events-none absolute inset-0 bg-gradient-to-r from-transparent via-pink-400/5 to-transparent"
          animate={{ x: ['-100%', '100%'] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'linear' }}
        />
      )}

      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-200">Render</span>
        {elapsed > 0 && (
          <span className="text-xs text-gray-400" data-testid="render-elapsed">
            {formatElapsed(elapsed)}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1.5 text-xs">
        {state === 'error' ? (
          <span
            data-testid="render-error"
            className="flex items-center gap-1 text-red-400"
          >
            <svg
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
            Failed
          </span>
        ) : state === 'done' ? (
          <span
            data-testid="render-done"
            className="flex items-center gap-1 text-green-400"
          >
            <motion.svg
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 300, damping: 20 }}
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </motion.svg>
            Done
          </span>
        ) : (
          <span
            className="animate-pulse text-pink-300"
            data-testid="render-substep"
          >
            {subStepLabel}
          </span>
        )}
      </div>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// LogPanel
// ---------------------------------------------------------------------------

interface LogPanelProps {
  lines: string[]
}

function LogPanel({ lines }: LogPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [paused, setPaused] = useState(false)
  const pausedRef = useRef(false)
  pausedRef.current = paused

  // Auto-scroll whenever new lines arrive, unless paused
  useEffect(() => {
    if (pausedRef.current) return
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  return (
    <div className="mt-6">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Log
        </span>
        <button
          onClick={() => setPaused((p) => !p)}
          className="rounded px-2 py-0.5 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200"
          data-testid="log-pause-toggle"
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>
      <div
        ref={containerRef}
        data-testid="log-panel"
        className="h-56 overflow-y-auto rounded-lg border border-gray-800 bg-gray-950 p-3 font-mono text-xs"
      >
        {lines.length === 0 ? (
          <span className="text-gray-600">No output yet…</span>
        ) : (
          lines.map((line, i) => (
            <div
              key={i}
              data-log-source={getLineSource(line)}
              className={getTagColor(line)}
            >
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main JobView component
// ---------------------------------------------------------------------------

export default function JobView({ jobId }: Props) {
  const [job, setJob] = useState<JobData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const [gatherStatus, setGatherStatus] = useState<
    Record<string, GatherSourceStatus>
  >({})

  // SSE-derived phase index — tracks the highest phase observed in SSE lines.
  const [ssePhaseIdx, setSsePhaseIdx] = useState(-1)

  // Per-source error/done state derived from SSE stream.
  const [sseErroredSources, setSseErroredSources] = useState<Set<string>>(new Set())

  // Track when each phase started (client-side) for elapsed timers
  const phaseStartRef = useRef<Partial<Record<string, number>>>({})
  const [phaseElapsed, setPhaseElapsed] = useState<Record<string, number>>({})
  const prevPhaseRef = useRef<string | null>(null)

  // Per-source start/done timestamps for frozen elapsed timers on gather cards.
  const sourceStartTimes = useRef<Partial<Record<string, number>>>({})
  const sourceDoneTimes = useRef<Partial<Record<string, number>>>({})
  const [sourceElapsed, setSourceElapsed] = useState<Record<string, number>>({})

  // Per-analyze-step timestamps and state for elapsed timers and card state.
  const analyzeStepStartTimes = useRef<Partial<Record<string, number>>>({})
  const analyzeStepDoneTimes = useRef<Partial<Record<string, number>>>({})
  const [analyzeStepState, setAnalyzeStepState] = useState<
    Partial<Record<string, AnalyzeStepState>>
  >({})
  const [analyzeStepElapsed, setAnalyzeStepElapsed] = useState<Record<string, number>>({})
  const [sectionsProgress, setSectionsProgress] = useState<SectionsProgress | null>(null)

  // Render phase state tracking — persists into done/error so the card remains
  // visible with a checkmark (done) or X (error) after the phase completes.
  const [renderPhaseState, setRenderPhaseState] = useState<RenderStepState | null>(null)
  const [renderSubStep, setRenderSubStep] = useState<'docx' | 'pdf'>('docx')
  const renderStartRef = useRef<number | null>(null)
  const renderDoneRef = useRef<number | null>(null)
  const [renderElapsed, setRenderElapsed] = useState(0)

  // Ref to current job so the SSE error handler can check status without a stale closure
  const jobStatusRef = useRef<'running' | 'done' | 'failed' | 'cancelled' | 'awaiting_clarification'>('running')

  // ---------------------------------------------------------------------------
  // Parse a Progress Line, update gather/analyze/render status, derive phase
  // ---------------------------------------------------------------------------
  const parseLine = useCallback((line: string) => {
    const tagMatch = line.match(/^\[([^\]]+)\]/)
    if (tagMatch) {
      const pIdx = sseTagToPhaseIdx(tagMatch[1])
      if (pIdx >= 0) {
        const phaseName = PHASES[pIdx]
        if (!phaseStartRef.current[phaseName]) {
          phaseStartRef.current[phaseName] = Date.now()
        }
        setSsePhaseIdx((prev) => Math.max(prev, pIdx))
      }

      // --- Render sub-step detection ---
      // First [render] line → mark phase as running and record start time.
      // Lines containing PDF/convert keywords → switch sub-step to 'pdf'.
      if (tagMatch[1] === 'render') {
        if (!renderStartRef.current) {
          renderStartRef.current = Date.now()
        }
        setRenderPhaseState((prev) => (prev === null ? 'running' : prev))
        if (RENDER_PDF_RE.test(line)) {
          setRenderSubStep('pdf')
        }
      }
    }

    // --- Gather source progress ---
    const m = line.match(PROGRESS_RE)
    if (m) {
      const source = m[1]
      const step = parseInt(m[2], 10)
      const total = parseInt(m[3], 10)
      const angle = m[4].trim()

      if (!sourceStartTimes.current[source]) {
        sourceStartTimes.current[source] = Date.now()
      }
      if (step === total) {
        sourceDoneTimes.current[source] = Date.now()
      }

      setGatherStatus((prev) => ({
        ...prev,
        [source]: { step, total, angle },
      }))
    }

    // Parse "[gather] degraded coverage: src1, src2 failed" to mark errored sources
    const degradedMatch = line.match(DEGRADED_COVERAGE_RE)
    if (degradedMatch) {
      const srcs = degradedMatch[1].split(',').map((s) => s.trim()).filter(Boolean)
      setSseErroredSources(new Set(srcs))
      const now = Date.now()
      for (const src of srcs) {
        if (!sourceDoneTimes.current[src]) {
          sourceDoneTimes.current[src] = now
        }
      }
    }

    // --- Analyze step detection ---
    const analyzeMatch = line.match(ANALYZE_STEP_RE)
    if (analyzeMatch) {
      const stepTag = analyzeMatch[1]
      const stepIdx = ANALYZE_STEPS.indexOf(stepTag as AnalyzeStep)
      const now = Date.now()

      if (!analyzeStepStartTimes.current[stepTag]) {
        analyzeStepStartTimes.current[stepTag] = now
      }

      setAnalyzeStepState((prev) => {
        const next: Partial<Record<string, AnalyzeStepState>> = { ...prev }
        // All earlier steps that were running are now done
        for (let i = 0; i < stepIdx; i++) {
          const earlier = ANALYZE_STEPS[i]
          if (next[earlier] === 'running') {
            next[earlier] = 'done'
            if (!analyzeStepDoneTimes.current[earlier]) {
              analyzeStepDoneTimes.current[earlier] = now
            }
          }
        }
        // Mark this step as running only if it hasn't been marked done/error already
        if (!next[stepTag] || next[stepTag] === ('pending' as never)) {
          next[stepTag] = 'running'
        }
        return next
      })
    }

    // --- Sections progress ---
    const sectionsMatch = line.match(SECTIONS_PROGRESS_RE)
    if (sectionsMatch) {
      const current = parseInt(sectionsMatch[1], 10)
      const total = parseInt(sectionsMatch[2], 10)
      const title = sectionsMatch[3]
      setSectionsProgress({ current, total, title })

      // When the final section is written, transition sections step to done
      if (current === total) {
        const now = Date.now()
        if (!analyzeStepDoneTimes.current['sections']) {
          analyzeStepDoneTimes.current['sections'] = now
        }
        setAnalyzeStepState((prev) => {
          if (prev['sections'] !== 'running') return prev
          return { ...prev, sections: 'done' }
        })
      }
    }
  }, [])

  // ---------------------------------------------------------------------------
  // When analyze phase ends (ssePhaseIdx advances past 2), mark any still-running
  // analyze steps as done.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (ssePhaseIdx <= 2) return
    const now = Date.now()
    setAnalyzeStepState((prev) => {
      if (!Object.values(prev).some((s) => s === 'running')) return prev
      const next: Partial<Record<string, AnalyzeStepState>> = { ...prev }
      for (const step of ANALYZE_STEPS) {
        if (next[step] === 'running') {
          next[step] = 'done'
          if (!analyzeStepDoneTimes.current[step]) {
            analyzeStepDoneTimes.current[step] = now
          }
        }
      }
      return next
    })
  }, [ssePhaseIdx])

  // ---------------------------------------------------------------------------
  // When the job fails while in the analyze phase, mark the running step as error.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (job?.status !== 'failed' || ssePhaseIdx !== 2) return
    setAnalyzeStepState((prev) => {
      if (!Object.values(prev).some((s) => s === 'running')) return prev
      const next: Partial<Record<string, AnalyzeStepState>> = { ...prev }
      for (const step of ANALYZE_STEPS) {
        if (next[step] === 'running') {
          next[step] = 'error'
        }
      }
      return next
    })
  }, [job?.status, ssePhaseIdx])

  // ---------------------------------------------------------------------------
  // When the Job reaches done, freeze the render card in 'done' state.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (job?.status !== 'done') return
    const now = Date.now()
    setRenderPhaseState((prev) => {
      if (prev !== 'running') return prev
      if (!renderDoneRef.current) renderDoneRef.current = now
      return 'done'
    })
  }, [job?.status])

  // ---------------------------------------------------------------------------
  // When the Job fails during the render phase, show the error state on the card.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (job?.status !== 'failed' || ssePhaseIdx !== 3) return
    setRenderPhaseState((prev) => (prev === 'running' ? 'error' : prev))
  }, [job?.status, ssePhaseIdx])

  // ---------------------------------------------------------------------------
  // Immediately freeze renderElapsed when the render card transitions to done.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (renderPhaseState !== 'done') return
    if (!renderStartRef.current || !renderDoneRef.current) return
    setRenderElapsed(Math.floor((renderDoneRef.current - renderStartRef.current) / 1000))
  }, [renderPhaseState])

  // ---------------------------------------------------------------------------
  // SSE connection
  // ---------------------------------------------------------------------------
  useEffect(() => {
    let es: EventSource | null = null
    let closed = false

    function connect() {
      es = new EventSource(`/api/jobs/${jobId}/stream`)

      es.onmessage = (evt) => {
        const line: string = evt.data
        setLogLines((prev) => [...prev, line])
        parseLine(line)
      }

      es.onerror = () => {
        if (!closed) {
          es?.close()
          setTimeout(() => {
            if (!closed && jobStatusRef.current === 'running') connect()
          }, 2000)
        }
      }
    }

    connect()

    return () => {
      closed = true
      es?.close()
    }
  }, [jobId, parseLine])

  // ---------------------------------------------------------------------------
  // Job polling
  // ---------------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const r = await fetch(`/api/jobs/${jobId}`, { credentials: 'include' })
        if (!r.ok) {
          setError(`Failed to load job (${r.status})`)
          return
        }
        const data: JobData = await r.json()
        if (!cancelled) {
          jobStatusRef.current = data.status
          setJob(data)
          if (data.gather_status) {
            setGatherStatus((prev) => ({ ...data.gather_status!, ...prev }))
          }
          if (data.status === 'running') {
            setTimeout(poll, 2000)
          }
        }
      } catch {
        if (!cancelled) setTimeout(poll, 3000)
      }
    }

    poll()
    return () => {
      cancelled = true
    }
  }, [jobId])

  // ---------------------------------------------------------------------------
  // Phase start time tracking from polled data (fallback if SSE lines are missed)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!job) return
    const phase = job.current_phase
    if (phase && phase !== prevPhaseRef.current) {
      if (!phaseStartRef.current[phase]) {
        phaseStartRef.current[phase] = Date.now()
      }
      prevPhaseRef.current = phase
    }
  }, [job])

  // ---------------------------------------------------------------------------
  // Elapsed timer — ticks every second while job is running
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!job || job.status !== 'running') return

    const interval = setInterval(() => {
      const now = Date.now()

      // Phase-level elapsed
      const updated: Record<string, number> = {}
      for (const [phase, startMs] of Object.entries(phaseStartRef.current)) {
        if (startMs) updated[phase] = Math.floor((now - startMs) / 1000)
      }
      setPhaseElapsed(updated)

      // Per-source elapsed — frozen at doneTime when the source finished
      const srcElapsed: Record<string, number> = {}
      for (const source of GATHER_SOURCES) {
        const startMs = sourceStartTimes.current[source]
        if (!startMs) continue
        const doneMs = sourceDoneTimes.current[source]
        srcElapsed[source] = Math.floor(((doneMs ?? now) - startMs) / 1000)
      }
      setSourceElapsed(srcElapsed)

      // Per-analyze-step elapsed — frozen at doneTime when the step finished
      const stepEl: Record<string, number> = {}
      for (const step of ANALYZE_STEPS) {
        const startMs = analyzeStepStartTimes.current[step]
        if (!startMs) continue
        const doneMs = analyzeStepDoneTimes.current[step]
        stepEl[step] = Math.floor(((doneMs ?? now) - startMs) / 1000)
      }
      setAnalyzeStepElapsed(stepEl)

      // Render elapsed — frozen at renderDoneRef when the phase finished
      if (renderStartRef.current) {
        const rDone = renderDoneRef.current
        setRenderElapsed(Math.floor(((rDone ?? now) - renderStartRef.current) / 1000))
      }
    }, 1000)

    return () => clearInterval(interval)
  }, [job?.status])

  // ---------------------------------------------------------------------------
  // Effective phase calculations
  // ---------------------------------------------------------------------------

  const polledPhaseIdx = phaseIndex(job?.current_phase ?? null)
  const timelinePhaseIdx = Math.max(polledPhaseIdx, ssePhaseIdx)
  const timelinePhase: Phase | null =
    timelinePhaseIdx >= 0 ? PHASES[timelinePhaseIdx] : null

  const cardPhase: Phase | null =
    ssePhaseIdx >= 0
      ? PHASES[ssePhaseIdx]
      : (job?.current_phase as Phase | null) ?? null

  // ---------------------------------------------------------------------------
  // Cancel and clarification handlers
  // ---------------------------------------------------------------------------

  const [cancelling, setCancelling] = useState(false)
  const [clarificationDraft, setClarificationDraft] = useState('')
  const [submittingClarification, setSubmittingClarification] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function handleCancel() {
    if (!job || cancelling) return
    if (!window.confirm('Cancel this job? Progress so far will be preserved in History.')) return
    setCancelling(true)
    setActionError(null)
    try {
      const resp = await fetch(`/api/jobs/${job.id}/cancel`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!resp.ok) {
        const data = await resp.json().catch(() => null)
        setActionError(data?.detail ?? 'Cancel failed.')
      }
    } catch {
      setActionError('Could not reach the server.')
    } finally {
      setCancelling(false)
    }
  }

  async function handleClarificationSubmit() {
    if (!job || submittingClarification) return
    const trimmed = clarificationDraft.trim()
    if (!trimmed) {
      setActionError('Please type a response before submitting.')
      return
    }
    setSubmittingClarification(true)
    setActionError(null)
    try {
      const resp = await fetch(`/api/jobs/${job.id}/respond`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response: trimmed }),
      })
      if (resp.ok) {
        setClarificationDraft('')
        // The job is now running again — the polling effect will pick it up.
      } else {
        const data = await resp.json().catch(() => null)
        setActionError(data?.detail ?? 'Failed to submit response.')
      }
    } catch {
      setActionError('Could not reach the server.')
    } finally {
      setSubmittingClarification(false)
    }
  }

  const [resynthesizing, setResynthesizing] = useState(false)

  async function handleResynthesize() {
    if (!job || resynthesizing) return
    if (!window.confirm(
      'Re-generate the report? This re-runs the analysis and rendering on the existing ' +
      'fetched data — no new web/API queries are made for Perplexity, xAI, Trends, or FMP, ' +
      'but it does spend on Claude calls (~$2-5).'
    )) return
    setResynthesizing(true)
    setActionError(null)
    try {
      const resp = await fetch(`/api/jobs/${job.id}/resynthesize`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!resp.ok) {
        const data = await resp.json().catch(() => null)
        setActionError(data?.detail ?? 'Re-generate failed.')
      }
      // Polling effect will pick up the new running state.
    } catch {
      setActionError('Could not reach the server.')
    } finally {
      setResynthesizing(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (error) {
    return (
      <div className="bg-gray-950 p-8 text-white">
        <p className="text-red-400">{error}</p>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="bg-gray-950 p-8 text-gray-400">Loading…</div>
    )
  }

  return (
    <div className="bg-gray-950 p-8 text-white">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <h1 className="max-w-2xl truncate text-2xl font-semibold">{job.subject}</h1>
        <div className="flex shrink-0 items-center gap-2">
          {(job.status === 'running' || job.status === 'awaiting_clarification') && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/20 disabled:opacity-50"
              data-testid="cancel-job-button"
            >
              {cancelling ? 'Cancelling…' : 'Cancel job'}
            </button>
          )}
          {(job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') && (
            <button
              onClick={handleResynthesize}
              disabled={resynthesizing}
              className="rounded border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-sm text-indigo-200 hover:bg-indigo-500/20 disabled:opacity-50"
              data-testid="resynthesize-button"
              title="Re-run the analysis and rendering on this job's fetched data. Skips the fetch phase."
            >
              {resynthesizing ? 'Starting…' : 'Re-generate report'}
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <div className="mt-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {actionError}
        </div>
      )}

      {/* Clarification block — surfaced when planner asked for more info */}
      {job.status === 'awaiting_clarification' && (
        <div
          className="mt-4 rounded-lg border border-blue-500/40 bg-blue-500/10 p-5"
          data-testid="clarification-block"
        >
          <h2 className="mb-2 text-lg font-semibold text-blue-200">
            The planner needs more information
          </h2>
          <p className="mb-4 text-sm text-blue-100/80">
            Your subject was too vague for the planner to design a useful research brief. Please
            answer the questions below — your response will be added to the subject and the
            planner will try again.
          </p>
          <ul className="mb-4 space-y-2 text-sm text-blue-100">
            {job.clarification_questions.map((q, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-blue-400">{i + 1}.</span>
                <span>{q}</span>
              </li>
            ))}
          </ul>
          <textarea
            value={clarificationDraft}
            onChange={(e) => setClarificationDraft(e.target.value)}
            placeholder="Type your clarification here. You can address all the questions in one response."
            className="block w-full rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white placeholder:text-gray-500 focus:border-blue-500 focus:outline-none"
            rows={5}
            data-testid="clarification-input"
          />
          <div className="mt-3 flex gap-2">
            <button
              onClick={handleClarificationSubmit}
              disabled={submittingClarification || !clarificationDraft.trim()}
              className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              data-testid="clarification-submit"
            >
              {submittingClarification ? 'Resuming…' : 'Submit and resume'}
            </button>
          </div>
        </div>
      )}

      {/* Degraded Coverage banner */}
      {job.degraded_sources.length > 0 && (
        <div
          className="mt-4 rounded border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-yellow-300"
          data-testid="degraded-coverage-banner"
        >
          <span className="font-semibold">Degraded coverage</span>
          {' — '}
          {job.degraded_sources.join(', ')} failed
        </div>
      )}

      {/* Cancelled banner */}
      {job.status === 'cancelled' && (
        <div className="mt-4 rounded border border-gray-500/40 bg-gray-500/10 px-4 py-3 text-gray-300">
          <span className="font-semibold">Cancelled.</span>
          {job.error_message ? ` ${job.error_message}` : ''}
        </div>
      )}

      {/* Error */}
      {job.status === 'failed' && job.error_message && (
        <div
          className="mt-4 rounded border border-red-500/40 bg-red-500/10 px-4 py-3 text-red-300"
          data-testid="error-message"
        >
          <span className="font-semibold">Error:</span> {job.error_message}
        </div>
      )}

      {/* Phase Timeline */}
      <div className="mt-8 overflow-x-auto">
        <PhaseTimeline
          currentPhase={timelinePhase}
          jobStatus={job.status}
          phaseElapsed={phaseElapsed}
        />
      </div>

      {/* Phase Cards / Results View */}
      <div className="mt-8">
        {/* Render Phase Card — persists through done/error so the checkmark or
            error state is visible after render completes. Shown outside the
            inner AnimatePresence so it coexists with the Results View. */}
        <AnimatePresence>
          {renderPhaseState !== null && (
            <motion.div
              key="render-section"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mb-6"
            >
              <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
                Rendering
              </p>
              <RenderCard
                state={renderPhaseState}
                subStep={renderSubStep}
                elapsed={renderElapsed}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Plan / Gather / Analyze cards and Results View */}
        <AnimatePresence mode="wait">
          {/* Gather: four source cards */}
          {job.status === 'running' && cardPhase === 'gather' && (
            <motion.div
              key="gather-cards"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
                Gathering data
              </p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {GATHER_SOURCES.map((source) => {
                  const srcStatus = gatherStatus[source]
                  const isError =
                    sseErroredSources.has(source) ||
                    (job.degraded_sources ?? []).includes(source)
                  const isDone =
                    !isError &&
                    srcStatus !== undefined &&
                    srcStatus.step === srcStatus.total &&
                    srcStatus.total > 0
                  return (
                    <GatherCard
                      key={source}
                      source={source}
                      status={srcStatus}
                      phaseElapsed={phaseElapsed['gather'] ?? 0}
                      sourceElapsed={sourceElapsed[source]}
                      phaseActive
                      isDone={isDone}
                      isError={isError}
                    />
                  )
                })}
              </div>
            </motion.div>
          )}

          {/* Analyze: one card per Step, sliding in as each Step starts */}
          {job.status === 'running' && cardPhase === 'analyze' && (
            <motion.div
              key="analyze-cards"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
            >
              <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
                Analyzing
              </p>
              <div className="space-y-3" data-testid="analyze-cards-container">
                <AnimatePresence>
                  {ANALYZE_STEPS.map((step) => {
                    const stepSt = analyzeStepState[step]
                    if (!stepSt) return null
                    return (
                      <AnalyzeStepCard
                        key={step}
                        step={step}
                        stepState={stepSt}
                        elapsed={analyzeStepElapsed[step] ?? 0}
                        sectionsProgress={step === 'sections' ? sectionsProgress : null}
                      />
                    )
                  })}
                </AnimatePresence>
              </div>
            </motion.div>
          )}

          {/* Plan: simple status card */}
          {job.status === 'running' && cardPhase === 'plan' && (
            <motion.div
              key="plan-card"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              data-testid="plan-card"
              className="rounded-lg border border-gray-700 bg-gray-800/50 p-4"
            >
              <div className="flex items-center gap-3">
                <motion.div
                  className="h-2 w-2 rounded-full bg-blue-400"
                  animate={{ opacity: [1, 0.3, 1] }}
                  transition={{ duration: 1, repeat: Infinity }}
                />
                <span className="text-sm text-gray-300">
                  Planning research
                  {(phaseElapsed['plan'] ?? 0) > 0 && (
                    <span
                      className="ml-2 text-gray-500"
                      data-testid="phase-elapsed-plan-inline"
                    >
                      {formatElapsed(phaseElapsed['plan'])}
                    </span>
                  )}
                </span>
              </div>
            </motion.div>
          )}

          {/* Done — Results View */}
          {job.status === 'done' && (
            <ResultsView key="results-view" job={job} />
          )}
        </AnimatePresence>
      </div>

      {/* Log Panel */}
      {(logLines.length > 0 || job.status === 'running') && (
        <LogPanel lines={logLines} />
      )}

      {/* Phase indicator */}
      {job.status === 'running' && timelinePhaseIdx >= 0 && (
        <p className="mt-4 text-xs text-gray-600">
          Phase {timelinePhaseIdx + 1}/4 — {timelinePhase}
        </p>
      )}
    </div>
  )
}
