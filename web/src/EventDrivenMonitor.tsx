import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type Run = {
  run_id: string
  scenario: string
  seed: number
  arrival_model: string
  status: string
  created_at: string
  config: Record<string, unknown>
  error?: string | null
}
type StreamEvent = {
  sequence: number
  evidence_id: string
  simulated_at: string
  source: string
  event_type: string
  payload: Record<string, any>
}
type Evidence = { evidence_id: string; source: string; observed_at: string; summary: string }
type Recommendation = { category: string; runbook_id: string | null; title: string; next_step: string; verification: string; supporting_evidence: string[] }
type Report = {
  status: string
  summary: string
  investigator_mode?: string
  hypotheses: Array<{ rank: number; title: string; explanation: string; supporting_evidence: string[]; contradicting_evidence: string[] }>
  evidence: Evidence[]
  unresolved_questions: string[]
  recommendations?: Recommendation[]
  search_activity?: Array<{
    category?: string | null; query: string
    sources: Array<{ source: string; label: string; count: number; error?: string | null }>
    documents: Array<{ title: string; snippet: string; source?: string; section_title?: string; page_number?: number; retrieval: Record<string, unknown> }>
    available_through?: string | null
  }>
}
type Job = { job_id: string; reason: string; cutoff: string; status: string; created_at: string; error?: string | null; report?: Report | null }
type Incident = {
  incident_id: string; stream_id: string; category: string; status: string
  opened_at: string; latest_window_end: string; episode: Record<string, any>
  investigations: Job[]
}
type IncidentView = { incident: Incident; latestJob?: Job; lastReport?: Job }
type Health = { investigator_mode: string; openai_key_configured: boolean }
type AlertLevel = 'normal' | 'medium' | 'high'
type DetectorScore = { detector: string; score: number; threshold: number; anomalous: boolean }
type ScorePoint = { score: number; threshold: number }

const categoryNames = ['payments', 'orders', 'account', 'delivery'] as const
const scenarioNames: Record<string, string> = {
  payment_issue: 'Payment authorization failure', healthy: 'Healthy control',
  unrelated_issue: 'Unrelated issue distractor', resolved_issue: 'Resolved payment issue',
  orders_queue_backlog: 'Order processing backlog', delivery_carrier_degradation: 'Carrier service degradation',
}
const sourceNames: Record<string, string> = {
  support_tickets: 'Customer ticket', product_logs: 'Service log', issue_tracker: 'Tracked issue',
  change_log: 'Change record', detector: 'Alert', runbooks: 'Operating guide', knowledge_docs: 'Reference document',
}

function formatTime(value?: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString('en-GB', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }) + ' UTC'
}
const alertLevelNames: Record<AlertLevel, string> = {
  normal: 'Normal', medium: 'Medium spike', high: 'High spike',
}

function runAlertLevel(value?: Run | null): AlertLevel {
  const severity = value?.config.incident_severity
  return severity === 'mild' ? 'medium' : severity === 'severe' ? 'high' : 'normal'
}
const detectorNames: Record<string, string> = {
  standardized_residual: 'Z-score', cusum: 'CUSUM',
  gamma_poisson_bayes_factor: 'Bayes factor', poisson_llr: 'Poisson rate check',
  negative_binomial_llr: 'Negative-binomial rate check',
}

function retainMonitorEvent(event: StreamEvent) {
  if (['window_observation', 'anomaly_alert', 'replay_progress', 'detector_baseline_ready', 'detector_baseline_status'].includes(event.event_type)) return true
  if (event.event_type === 'window_score') return event.payload.calibration !== true
  return event.event_type === 'ticket_created' && event.payload.replay_phase === 'stream'
}

function formatFactor(logValue: number) {
  const power = logValue / Math.LN10
  if (!Number.isFinite(power)) return '—'
  if (power >= 6) return `10^${power.toFixed(1)}×`
  return `${(10 ** power).toPrecision(3)}×`
}

function Sparkline({ points }: { points: ScorePoint[] }) {
  if (!points.length) return <span className="score-spark-empty">Waiting for first window</span>
  const values = points.slice(-144).map(({ score, threshold }) => {
    const safeThreshold = Math.max(Math.abs(threshold), 1e-9)
    return score / safeThreshold
  })
  const width = 112, height = 25, pad = 2
  const coords = values.map((value, index) => {
    const x = values.length <= 1 ? width : pad + index * (width - pad * 2) / (values.length - 1)
    const clipped = Math.max(-0.2, Math.min(1.5, value))
    const y = height - pad - ((clipped + 0.2) / 1.7) * (height - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const cutoffY = height - pad - ((1 + 0.2) / 1.7) * (height - pad * 2)
  return <svg className="score-sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Recent score history; horizontal line is the alert cutoff">
    <line x1="0" x2={width} y1={cutoffY} y2={cutoffY} className="score-spark-threshold" />
    {values.length > 1 && <polyline points={coords} className="score-spark-path" />}
    <circle cx={coords.split(' ').at(-1)?.split(',')[0]} cy={coords.split(' ').at(-1)?.split(',')[1]} r="2" className="score-spark-current" />
  </svg>
}

function formatReplayClock(value?: string | null) {
  if (!value) return 'Waiting for stream'
  const date = new Date(value)
  const day = Math.floor((date.getTime() - Date.UTC(2026, 0, 1)) / 86_400_000) + 1
  const time = date.toLocaleTimeString('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
  return `Day ${day} · ${time} UTC`
}

function windowStartAt(index: number) {
  return new Date(Date.UTC(2026, 0, 1) + index * 30 * 60_000).toISOString()
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export default function EventDrivenMonitor() {
  const [runs, setRuns] = useState<Run[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [incidents, setIncidents] = useState<IncidentView[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [alertLevel, setAlertLevel] = useState<AlertLevel>('high')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('signalroom-theme') === 'light' ? 'light' : 'dark' } catch { return 'dark' }
  })
  const [busy, setBusy] = useState(false)
  const [controlBusy, setControlBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const lastSequence = useRef(0)
  const startupTask = useRef<Promise<void> | null>(null)
  const runRef = useRef<Run | null>(null)
  const eventsRef = useRef<StreamEvent[]>([])
  const pendingEvents = useRef<StreamEvent[]>([])
  const eventFlushFrame = useRef<number | null>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('signalroom-theme', theme) } catch { /* keep the session theme */ }
  }, [theme])
  useEffect(() => { runRef.current = run }, [run])
  useEffect(() => { eventsRef.current = events }, [events])

  const loadRun = useCallback(async (runId: string) => {
    const savedRun = await jsonRequest<Run>(`/api/runs/${encodeURIComponent(runId)}`)
    const restored: StreamEvent[] = []
    let cursor = 0
    while (true) {
      const page = await jsonRequest<StreamEvent[]>(`/api/runs/${encodeURIComponent(runId)}/events?after_sequence=${cursor}&limit=2000`)
      restored.push(...page.filter(retainMonitorEvent))
      if (page.length) cursor = page[page.length - 1].sequence
      if (page.length < 2000) break
    }
    lastSequence.current = cursor || 0
    setRun(savedRun)
    setAlertLevel(runAlertLevel(savedRun))
    setEvents(restored)
    setError('')
  }, [])

  useEffect(() => {
    if (!startupTask.current) {
      startupTask.current = (async () => {
        const [recent, currentHealth] = await Promise.all([
          jsonRequest<Run[]>('/api/runs?limit=20'),
          jsonRequest<Health>('/api/health'),
        ])
        setRuns(recent)
        setHealth(currentHealth)
        const requestedRun = new URLSearchParams(window.location.search).get('run')
        const selected = requestedRun && recent.find((item) => item.run_id === requestedRun)
        if (selected) {
          await loadRun(selected.run_id)
          return
        }

        // A plain page load prepares the demo controls. It must not silently
        // choose a random replay or start an OpenAI investigation.
        const activeRuns = recent.filter((item) => Number(item.config.monitor_history_windows ?? 0) > 0
          && ['preparing', 'running', 'queued', 'paused', 'stopping'].includes(item.status))
        await Promise.all(activeRuns.map((item) => jsonRequest(
          `/api/runs/${encodeURIComponent(item.run_id)}/controls/stop`, { method: 'POST' },
        ).catch(() => undefined)))
        setRun(null)
        setEvents([])
        setIncidents([])
      })().catch((issue) => {
        setError(issue instanceof Error ? issue.message : 'Could not start a fresh replay.')
      })
    }
  }, [loadRun])

  useEffect(() => {
    if (!run) return
    const stream = new EventSource(`/api/runs/${encodeURIComponent(run.run_id)}/stream?after_sequence=${lastSequence.current}`)
    stream.onopen = () => setConnected(true)
    stream.onerror = () => setConnected(false)
    stream.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as StreamEvent
        if (event.sequence <= lastSequence.current) return
        lastSequence.current = event.sequence
        if (!retainMonitorEvent(event)) return
        pendingEvents.current.push(event)
        if (eventFlushFrame.current === null) {
          eventFlushFrame.current = window.requestAnimationFrame(() => {
            const batch = pendingEvents.current.splice(0)
            eventFlushFrame.current = null
            if (batch.length) setEvents((current) => [...current, ...batch])
          })
        }
      } catch { setError('The event stream returned an unreadable update.') }
    }
    return () => {
      stream.close(); setConnected(false)
      if (eventFlushFrame.current !== null) window.cancelAnimationFrame(eventFlushFrame.current)
      eventFlushFrame.current = null
      pendingEvents.current = []
    }
  }, [run?.run_id])

  useEffect(() => {
    if (!run || !['preparing', 'running', 'queued', 'paused', 'stopping'].includes(run.status)) return
    const timer = window.setInterval(() => {
      jsonRequest<Run>(`/api/runs/${encodeURIComponent(run.run_id)}`).then(setRun).catch(() => undefined)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [run?.run_id, run?.status])

  const refreshIncidents = useCallback(async () => {
    if (!run) { setIncidents([]); return }
    try {
      const found = await jsonRequest<Incident[]>(`/api/incidents?stream_id=${encodeURIComponent(run.run_id)}`)
      const views = await Promise.all(found.map(async (incident): Promise<IncidentView> => {
        const detail = await jsonRequest<Incident>(`/api/incidents/${encodeURIComponent(incident.incident_id)}`)
        const latestJob = detail.investigations.at(-1)
        const completed = [...detail.investigations].reverse().find((job) => job.status === 'completed')
        const [latest, lastReport] = await Promise.all([
          latestJob ? jsonRequest<Job>(`/api/jobs/${encodeURIComponent(latestJob.job_id)}`) : Promise.resolve(undefined),
          completed && completed.job_id !== latestJob?.job_id
            ? jsonRequest<Job>(`/api/jobs/${encodeURIComponent(completed.job_id)}`)
            : Promise.resolve(undefined),
        ])
        return { incident: detail, latestJob: latest, lastReport: lastReport ?? (latest?.status === 'completed' ? latest : undefined) }
      }))
      setIncidents(views)
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not refresh incident updates.') }
  }, [run?.run_id])

  useEffect(() => {
    void refreshIncidents()
    const timer = window.setInterval(() => void refreshIncidents(), 2200)
    return () => window.clearInterval(timer)
  }, [refreshIncidents])

  const observations = useMemo(() => events.filter((item) => item.event_type === 'window_observation'), [events])
  const latestObservation = observations.at(-1)
  const scoreEvents = useMemo(() => events.filter((item) => item.event_type === 'window_score'), [events])
  const latestScore = scoreEvents.at(-1)
  const scores = (latestScore?.payload.scores_by_category ?? {}) as Record<string, { expected?: number; alerted?: boolean; scores?: DetectorScore[] }>
  const baselineEvent = events.filter((item) => item.event_type === 'detector_baseline_ready' || item.event_type === 'detector_baseline_status').at(-1)
  const baseline = baselineEvent?.payload
  const baselineCategories = (baseline?.categories ?? {}) as Record<string, { expected_count?: number | null; thresholds?: Record<string, number> }>
  const scoreHistory = useMemo(() => {
    const result: Record<string, Record<string, ScorePoint[]>> = Object.fromEntries(categoryNames.map((category) => [category, {}]))
    for (const event of scoreEvents) {
      const byCategory = event.payload.scores_by_category ?? {}
      for (const category of categoryNames) {
        for (const score of (byCategory[category]?.scores ?? []) as DetectorScore[]) {
          if (!['standardized_residual', 'cusum', 'gamma_poisson_bayes_factor'].includes(score.detector)) continue
          result[category][score.detector] ??= []
          result[category][score.detector].push({ score: Number(score.score), threshold: Number(score.threshold) })
        }
      }
    }
    return result
  }, [scoreEvents])
  const alertEvents = useMemo(() => events.filter((item) => item.event_type === 'anomaly_alert'), [events])
  const latestProgress = events.filter((item) => item.event_type === 'replay_progress').at(-1)
  const liveWeek = Number(run?.config.monitor_history_windows ?? 0) > 0
  const historyWindows = Number(run?.config.monitor_history_windows ?? 192)
  const latestCompletedIndex = Number(latestObservation?.payload.window_index ?? historyWindows - 1)
  const currentWindowIndex = liveWeek
    ? (run?.status === 'completed' ? latestCompletedIndex : Number(latestProgress?.payload.current_window_index ?? historyWindows))
    : Number(latestObservation?.payload.window_index ?? -1) + 1
  const liveCounts = useMemo(() => {
    const result: Record<string, number> = Object.fromEntries(categoryNames.map((category) => [category, 0]))
    if (!liveWeek) return result
    for (const event of events) {
      if (event.event_type !== 'ticket_created' || event.payload.replay_phase !== 'stream'
          || Number(event.payload.window_index) !== currentWindowIndex) continue
      const category = String(event.payload.category ?? '')
      if (category in result) result[category] += 1
    }
    return result
  }, [events, liveWeek, currentWindowIndex])
  const displayCounts = liveWeek && run?.status !== 'completed'
    ? liveCounts : (latestObservation?.payload.category_counts ?? {}) as Record<string, number>
  const alertingCategories = new Set(incidents.filter(({ incident }) => incident.status === 'active').map(({ incident }) => incident.category))
  const ticketSample = useMemo(() => events.filter((event) => event.event_type === 'ticket_created'
    && event.payload.replay_phase === 'stream').slice(-8).reverse(), [events])
  const streamCompletedWindows = liveWeek
    ? Math.max(0, Number(latestObservation?.payload.window_index ?? historyWindows - 1) - historyWindows + 1)
    : observations.length
  const currentRunMode = run?.config.investigation_mode === 'manual' ? 'manual' : 'automatic'
  const statusLabel = !run ? 'Ready to replay' : run.status === 'preparing' ? 'Preparing baseline'
    : run.status === 'running' ? 'Running' : run.status === 'completed' ? 'Replay complete' : run.status
  const runIsActive = Boolean(run && ['preparing', 'running', 'paused', 'stopping'].includes(run.status))
  const openAiReady = health?.investigator_mode === 'openai' && health.openai_key_configured === true
  const countsHeading = liveWeek
    ? run?.status === 'completed'
      ? `Last window · ${formatReplayClock(latestObservation?.payload.window_start)} – ${formatReplayClock(latestObservation?.payload.window_end)}`
      : `Day ${Math.floor(currentWindowIndex / 48) + 1} · ${formatTime(windowStartAt(currentWindowIndex))} – ${formatTime(new Date(Date.parse(windowStartAt(currentWindowIndex)) + 30 * 60_000).toISOString())}`
    : latestObservation ? `Last window completed: ${formatTime(latestObservation.payload.window_end)}` : 'Waiting for the first completed window'

  async function startReplay(seedOverride?: number, levelOverride?: AlertLevel) {
    if (busy) return
    setBusy(true); setError(''); setEvents([]); pendingEvents.current = []; setIncidents([]); lastSequence.current = 0
    try {
      const next = await jsonRequest<Run>('/api/runs', {
        method: 'POST', body: JSON.stringify({ randomized_week: true, alert_level: levelOverride ?? alertLevel,
          seed: seedOverride, arrival_model: 'poisson', investigation_mode: 'automatic' }),
      })
      setRun(next)
      setRuns((existing) => [next, ...existing.filter((item) => item.run_id !== next.run_id)].slice(0, 20))
      const url = new URL(window.location.href); url.search = ''; url.hash = ''
      window.history.replaceState(null, '', url)
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not start the replay.') }
    finally { setBusy(false) }
  }

  async function controlReplay(action: 'pause' | 'resume' | 'step' | 'stop') {
    if (!run || controlBusy) return
    setControlBusy(true)
    try {
      await jsonRequest(`/api/runs/${encodeURIComponent(run.run_id)}/controls/${action}`, { method: 'POST' })
      setRun(await jsonRequest<Run>(`/api/runs/${encodeURIComponent(run.run_id)}`))
    } catch (issue) {
      const message = issue instanceof Error ? issue.message : 'Could not control the replay.'
      if (message === 'Unknown run') {
        setRun(null)
        setEvents([])
        setIncidents([])
        setError('This replay is no longer available. Start a new demo to begin a fresh run.')
      } else setError(message)
    }
    finally { setControlBusy(false) }
  }

  async function investigate(incident: Incident) {
    try {
      setError('')
      await jsonRequest(`/api/incidents/${encodeURIComponent(incident.incident_id)}/investigations`, {
        method: 'POST', body: JSON.stringify({
          reason: 'Operator requested investigation', idempotency_key: crypto.randomUUID(),
        }),
      })
      await refreshIncidents()
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not start the investigation.') }
  }

  function episodeNumbers(incident: Incident) {
    const episode = incident.episode
    const start = Number(episode.start_window ?? 0)
    const end = Number(episode.latest_window ?? start)
    const relevant = alertEvents.filter((item) => item.payload.category === incident.category
      && Number(item.payload.window_index) >= start && Number(item.payload.window_index) <= end)
    return {
      windows: Math.max(Number(episode.window_count ?? 1), relevant.length),
      tickets: relevant.reduce((sum, item) => sum + Number(item.payload.ticket_count ?? 0), 0),
      expected: relevant.reduce((sum, item) => sum + Number(item.payload.expected ?? 0), 0),
    }
  }

  const setReplay = async (id: string) => {
    setBusy(true)
    try { await loadRun(id) } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not open this replay.') }
    finally { setBusy(false) }
  }

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand-lockup"><div className="brand-mark"><span /><span /><span /></div><div><div className="brand-name">signal<span>room</span></div><div className="brand-caption">OPERATIONS INTELLIGENCE</div></div></div>
      <div className="topbar-right"><div className={`connection-pill ${connected ? 'is-connected' : ''}`}><i />{connected ? 'EVENT STREAM CONNECTED' : 'SYNTHETIC REPLAY'}</div>
        <button className="theme-toggle" type="button" onClick={() => setTheme((value) => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☼ Light mode' : '☾ Dark mode'}</button><div className="avatar">AR</div></div>
    </header>
    <div className="monitor-shell"><section className="monitor-main">
      <div className="monitor-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> MONITORING / TICKET COUNTS</div><h1>Live monitor</h1>
        <p className="page-subtitle">Synthetic data · detector watches ticket counts; the investigator follows emitted alerts.</p></div>
        <div className="replay-status"><span className={`status-dot ${run?.status ?? 'ready'}`} />Synthetic replay · {statusLabel}<small>{run ? (liveWeek ? `${streamCompletedWindows}/144 stream windows · ${formatReplayClock(latestProgress?.payload.simulated_at ?? latestProgress?.simulated_at)}` : `${observations.length} windows completed`) : 'No replay started'}</small></div></div>
      {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError('')}>Dismiss</button></div>}

      <details className="replay-setup" open={!run}>
        <summary>{run ? 'Replay settings' : 'Choose a demo and start'}</summary>
        <div className="replay-fields">
          <label className="field"><span>Alert level</span><select value={alertLevel} onChange={(event) => setAlertLevel(event.target.value as AlertLevel)}>
            <option value="normal">Normal</option><option value="medium">Medium spike</option><option value="high">High spike</option>
          </select></label>
          <button className="primary-button" onClick={() => void startReplay()} disabled={busy || runIsActive || !openAiReady}>
            {busy ? 'Preparing…' : run ? 'Start new demo' : 'Start demo'}
          </button>
        </div>
        <p className="replay-note">{alertLevel === 'normal'
          ? 'No incident is planted; the detector still watches for unusual counts.'
          : `${alertLevelNames[alertLevel]} uses a seeded payment incident; the detector decides when it alerts.`} Four days prepare the baseline, then three days stream in about two minutes. An alert starts an OpenAI investigation automatically.</p>
        <p className={`openai-readiness ${openAiReady ? 'is-ready' : 'is-unavailable'}`} role="status">
          {!health ? 'Checking OpenAI setup…' : health.investigator_mode !== 'openai'
            ? 'OpenAI is required for this demo. Restart the app without offline mock mode.'
            : health.openai_key_configured ? 'OpenAI investigator · key detected' : 'OpenAI key not found. Add OPENAI_API_KEY to the project .env file.'}
        </p>
      </details>

      {run && <div className="replay-toolbar"><span>{liveWeek ? `${alertLevelNames[runAlertLevel(run)]} · seven-day seeded replay` : scenarioNames[run.scenario] ?? run.scenario} · seed {run.seed} · {currentRunMode} investigation</span>
        <div>{run.status === 'running' && <button disabled={controlBusy} onClick={() => void controlReplay('pause')}>Pause</button>}{run.status === 'paused' && <><button disabled={controlBusy} onClick={() => void controlReplay('resume')}>Resume</button><button disabled={controlBusy} onClick={() => void controlReplay('step')}>Step window</button></>}{['running', 'paused'].includes(run.status) && <button disabled={controlBusy} onClick={() => void controlReplay('stop')}>Stop</button>}{liveWeek && ['completed', 'stopped'].includes(run.status) && <button onClick={() => void startReplay(run.seed, runAlertLevel(run))} disabled={busy}>Replay this seed</button>}</div></div>}
      {run && liveWeek && <div className="replay-progress" role="progressbar" aria-valuenow={Math.round(Number(latestProgress?.payload.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${Math.round(Number(latestProgress?.payload.progress ?? 0) * 100)}%` }} /></div>}

      <section className="counts-panel panel" aria-label="Latest category ticket counts">
        <div className="counts-heading"><div><div className="section-kicker">{liveWeek && run?.status !== 'completed' ? 'LIVE WINDOW — TICKETS SO FAR' : liveWeek ? 'LAST COMPLETED WINDOW' : 'TICKET COUNTS — LAST 30 MINUTES'}</div><h2>{countsHeading}</h2></div><span>{run ? (liveWeek ? `${streamCompletedWindows}/144 windows streamed` : `${observations.length} windows`) : 'No replay'}</span></div>
        {liveWeek && <p className="counts-context">{baseline?.baseline_ready
          ? `Usual counts and alert cutoffs learned from ${Number(baseline.warmup_windows ?? historyWindows)} earlier windows. Scores update when each 30-minute window closes.`
          : 'Preparing the usual counts and alert cutoffs from earlier windows.'}</p>}
        <div className="count-table"><div className="count-row count-header"><span>Category</span><span>Tickets now</span><span>Usual / 30 min</span><span>Z-score</span><span>CUSUM</span><span>Bayes factor</span><span>Status</span></div>
          {categoryNames.map((category) => {
            const raised = alertingCategories.has(category) || scores[category]?.alerted === true
            const expected = baselineCategories[category]?.expected_count ?? scores[category]?.expected
            const hasCount = liveWeek ? run?.status !== 'preparing' && currentWindowIndex >= historyWindows : Boolean(latestObservation)
            const rowStatus = raised ? 'ALERTING' : run?.status === 'preparing' || !baseline?.baseline_ready ? 'Preparing' : run?.status === 'paused' ? 'Paused' : liveWeek && run?.status === 'running' ? 'Watching' : latestObservation ? 'Normal' : 'Waiting'
            const latestScores = scores[category]?.scores ?? []
            const metricCell = (detector: string, formatter: (value: number) => string = (value) => value.toFixed(2)) => {
              const value = latestScores.find((item) => item.detector === detector)
              const points = scoreHistory[category]?.[detector] ?? []
              const thresholdKey = detector === 'standardized_residual' ? 'z_score'
                : detector === 'cusum' ? 'cusum' : 'log_bayes_factor'
              const baselineCutoff = baselineCategories[category]?.thresholds?.[thresholdKey]
              if (!value) return <div className="score-cell" key={detector}>
                <span className="score-value">{baseline?.baseline_ready ? 'Waiting' : '—'}</span>
                {baselineCutoff != null && <small>cutoff {formatter(Number(baselineCutoff))}</small>}
                <Sparkline points={points} />
              </div>
              return <div className={`score-cell ${value.anomalous ? 'score-over-limit' : ''}`} key={detector}>
                <span className="score-value">{formatter(Number(value.score))}</span><small>cutoff {formatter(Number(value.threshold))}</small>
                <Sparkline points={points} />
              </div>
            }
            return <div className={`count-row ${raised ? 'count-alert' : ''}`} key={category}>
              <strong>{category}</strong><b>{hasCount ? Number(displayCounts[category] ?? 0) : '—'}</b>
              <span>{expected == null || !Number.isFinite(Number(expected)) ? 'preparing' : Number(expected).toFixed(1)}</span>
              {metricCell('standardized_residual')}{metricCell('cusum')}
              {metricCell('gamma_poisson_bayes_factor', formatFactor)}
              <span className={raised ? 'count-status-alert' : 'count-status-normal'}>{rowStatus}</span>
            </div>
          })}
        </div>
        {liveWeek && <p className="score-legend">Scores compare each completed window with its cutoff. The sparkline shows recent history; the dashed line is the cutoff.</p>}
      </section>

      {run && liveWeek && <section className="ticket-feed panel" aria-label="Recent arriving ticket sample">
        <div className="panel-heading"><div><div className="section-kicker">INCOMING TICKETS</div><h2>Recent arrivals <span>sample · totals include every ticket</span></h2></div><span className="record-count">{ticketSample.length} shown</span></div>
        <div className="ticket-feed-list" aria-live="polite">
          {ticketSample.length === 0 && <p className="ticket-feed-empty">{run.status === 'completed' ? 'Replay complete. No tickets were generated.' : 'Waiting for tickets from Day 5...'}</p>}
          {ticketSample.map((ticket) => <article className="ticket-feed-item" key={ticket.evidence_id}>
            <time>{formatReplayClock(ticket.simulated_at)}</time><strong>{String(ticket.payload.category ?? 'Ticket')}</strong>
            <div><b>{String(ticket.payload.title ?? 'Customer contact')}</b><p>{String(ticket.payload.body ?? '')}</p></div>
          </article>)}
        </div>
      </section>}

      <section className="incident-stream" aria-label="Alert and investigation updates">
        <div className="section-kicker">ALERTS AND INVESTIGATIONS</div>
        {!run && <p className="stream-empty">Start a replay to watch ticket counts and automatic investigations.</p>}
        {run && incidents.length === 0 && <p className="stream-empty">{run.status === 'completed' ? 'Replay complete · no alert episodes were emitted.' : 'No alert episodes yet. The detector is watching each completed window.'}</p>}
        {incidents.map(({ incident, latestJob, lastReport }) => {
          const totals = episodeNumbers(incident)
          const episode = incident.episode
          const triggerNames = [...new Set(alertEvents.filter((item) => item.payload.category === incident.category
            && Number(item.payload.window_index) >= Number(episode.start_window ?? 0)
            && Number(item.payload.window_index) <= Number(episode.latest_window ?? Number.MAX_SAFE_INTEGER))
            .flatMap((item) => (item.payload.detectors_triggered ?? []) as string[]))]
          const additionalChecks = triggerNames.filter((name) => ['poisson_llr', 'negative_binomial_llr'].includes(name))
          const report = lastReport?.report
          const evidenceMap = new Map((report?.evidence ?? []).map((item) => [item.evidence_id, item]))
          const recommendation = report?.recommendations?.find((item) => item.category === incident.category)
            ?? report?.recommendations?.[0]
          const retrievedGuides = report?.search_activity?.flatMap((activity) => activity.documents)
            .filter((document) => document.source === 'runbooks') ?? []
          return <article className={`incident-card ${incident.status === 'active' ? 'is-alerting' : 'is-ended'}`} key={incident.incident_id}>
            <header className="incident-card-heading"><div><div className="section-kicker">{incident.status === 'active' ? 'NEW ALERT' : 'ALERT EPISODE ENDED'} — {incident.category.toUpperCase()}</div>
              <h2>Episode began {formatTime(incident.opened_at)}</h2><p>{totals.windows} alert {totals.windows === 1 ? 'window' : 'windows'} · {totals.tickets} tickets vs {totals.expected.toFixed(1)} expected</p></div>
              <span className={`incident-state ${incident.status}`}>{incident.status === 'active' ? 'Alerting' : 'Ended'}</span></header>
            {additionalChecks.length > 0 && <details className="alert-detector-details"><summary>Alert details</summary><p>Additional rate checks crossed their cutoffs: {additionalChecks.map((name) => detectorNames[name] ?? name).join(', ')}.</p></details>}
            <div className="investigation-state" aria-live="polite">
              {latestJob?.status === 'queued' && <><strong>Investigation queued</strong><span>Waiting for the investigator to start.</span></>}
              {latestJob?.status === 'running' && <><strong>Investigation started automatically</strong><span>Comparing tickets, service logs, changes, and operating guides…</span><span>Evidence available through {formatTime(latestJob.cutoff)}</span></>}
              {latestJob?.status === 'failed' && <><strong>Investigation could not complete</strong><span>{latestJob.error ?? latestJob.reason}</span></>}
              {latestJob?.status === 'blocked' && <><strong>Investigation stopped for review</strong><span>{latestJob.error ?? 'Review before retrying to avoid an unintentional provider call.'}</span></>}
              {latestJob?.status === 'completed' && <span>Investigation completed · Evidence through {formatTime(latestJob.cutoff)}</span>}
              {!latestJob && currentRunMode === 'manual' && <><strong>Alert recorded</strong><span>Manual mode · investigation is waiting for an operator.</span></>}
              {!latestJob && currentRunMode === 'automatic' && <><strong>Investigation is starting</strong><span>Preparing evidence for this alert.</span></>}
            </div>
            {!latestJob && currentRunMode === 'manual' && <button className="primary-button" onClick={() => void investigate(incident)}>Investigate now</button>}
            {latestJob?.status === 'failed' && <button className="secondary-button" onClick={() => void investigate(incident)}>Retry investigation</button>}
            {report && <section className="monitor-report"><div className="section-kicker">REPORT</div><h3>{report.hypotheses?.[0]?.title ?? (report.status === 'abstained' ? 'Not enough evidence to explain the alert' : 'Investigation findings')}</h3>
              <p>{report.summary}</p>
              {recommendation && <section className="monitor-recommendation">
                <div className="section-kicker">RECOMMENDED NEXT STEP · ADVISORY</div>
                <h4>{recommendation.title}</h4>
                <p>{recommendation.next_step}</p>
                <p><strong>Verify:</strong> {recommendation.verification}</p>
                {retrievedGuides[0] && <small>Reference guide: {retrievedGuides[0].title}</small>}
                {recommendation.supporting_evidence.length > 0 && <details><summary>Why this step was recommended</summary>
                  {recommendation.supporting_evidence.map((id) => { const item = evidenceMap.get(id); return <p key={id}>{item ? `${sourceNames[item.source] ?? 'Evidence'} · ${item.summary}` : 'Referenced evidence record'}</p> })}
                </details>}
              </section>}
              <details className="report-evidence-details"><summary>Evidence and retrieved guides</summary>
                {report.hypotheses?.[0]?.explanation && <p>{report.hypotheses[0].explanation}</p>}
                {report.hypotheses?.[0]?.supporting_evidence?.length ? <div className="report-citations"><strong>Evidence supporting this explanation</strong>{report.hypotheses[0].supporting_evidence.map((id) => { const item = evidenceMap.get(id); return <details key={id}><summary>{item ? `${sourceNames[item.source] ?? 'Evidence'} · ${formatTime(item.observed_at)}` : 'Evidence record'}</summary>{item && <p>{item.summary}</p>}</details> })}</div> : null}
                {report.hypotheses?.[0]?.contradicting_evidence?.length ? <div className="report-citations"><strong>Evidence that does not fit</strong>{report.hypotheses[0].contradicting_evidence.map((id) => { const item = evidenceMap.get(id); return <details key={id}><summary>{item ? `${sourceNames[item.source] ?? 'Evidence'} · ${formatTime(item.observed_at)}` : 'Evidence record'}</summary>{item && <p>{item.summary}</p>}</details> })}</div> : null}
                {report.hypotheses?.length > 1 && <details className="other-explanations"><summary>Other explanations considered ({report.hypotheses.length - 1})</summary>{report.hypotheses.slice(1).map((hypothesis) => <div key={`${hypothesis.rank}-${hypothesis.title}`}><strong>{hypothesis.title}</strong><p>{hypothesis.explanation}</p></div>)}</details>}
                {report.search_activity?.length ? <div className="report-retrieval"><strong>Related records and guides</strong>{report.search_activity.map((activity, index) => <div key={`${activity.query}-${index}`}><span>{activity.category ?? incident.category} · “{activity.query}”</span><small>{activity.sources.map((item) => `${item.label}: ${item.error ? 'unavailable' : item.count}`).join(' · ')}</small>{activity.documents.map((doc, docIndex) => <details key={`${doc.title}-${docIndex}`}><summary>{doc.title}{doc.section_title ? ` · ${doc.section_title}` : ''}{doc.page_number ? ` · page ${doc.page_number}` : ''}</summary><p>{doc.snippet}</p></details>)}</div>)}</div> : <p>No guide or reference search was recorded.</p>}
              </details>
              {report.unresolved_questions?.length > 0 && <details className="report-unknown"><summary>What remains uncertain</summary><ul>{report.unresolved_questions.map((question, index) => <li key={index}>{question}</li>)}</ul></details>}
              {latestJob?.status === 'completed' && <small className="report-generator">Generated with {report.investigator_mode === 'offline_demo' ? 'offline demo' : report.investigator_mode === 'openai' ? 'OpenAI' : 'configured investigator'}.</small>}
            </section>}
            {lastReport && latestJob && latestJob.job_id !== lastReport.job_id && latestJob.status !== 'completed' && <details className="previous-report"><summary>Previous report · evidence through {formatTime(lastReport.cutoff)}</summary><p>{lastReport.report?.summary}</p></details>}
          </article>
        })}
      </section>

      {run && <details className="replay-history"><summary>Earlier replays ({runs.filter((item) => item.run_id !== run.run_id).length})</summary>{runs.filter((item) => item.run_id !== run.run_id).map((item) => <button key={item.run_id} onClick={() => void setReplay(item.run_id)} disabled={busy}><strong>{Number(item.config.monitor_history_windows ?? 0) > 0 ? 'Seven-day seeded replay' : scenarioNames[item.scenario] ?? item.scenario}</strong><span>{formatTime(item.created_at)} · {item.status} · seed {item.seed}</span></button>)}</details>}
      <footer className="monitor-footer">Synthetic data only · Ticket counts trigger alerts; evidence supports or limits the explanation.</footer>
    </section></div>
  </main>
}
