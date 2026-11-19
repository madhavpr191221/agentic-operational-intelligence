import { useCallback, useEffect, useId, useMemo, useState } from 'react'

type EventRecord = {
  sequence: number
  evidence_id: string
  simulated_at: string
  source: string
  event_type: string
  payload: Record<string, unknown>
}

type Run = {
  run_id: string
  scenario: string
  seed: number
  arrival_model: string
  status: string
  error?: string | null
}

type EvidenceItem = { evidence_id: string; source: string; observed_at: string; summary: string }

const evidenceSourceLabels: Record<string, string> = {
  detector: 'Detector alert',
  support_tickets: 'Customer ticket',
  product_logs: 'Service log',
  issue_tracker: 'Tracked issue',
  change_log: 'Change record',
}

const evidenceSourceDescriptions: Record<string, string> = {
  detector: 'Statistical result for the selected ticket-count window.',
  support_tickets: 'A synthetic customer message counted as one support ticket.',
  product_logs: 'A synthetic product or service event, such as an error.',
  issue_tracker: 'A fictional incident record and its current status.',
  change_log: 'A fictional deployment or configuration change record.',
}

const investigationToolLabels: Record<string, string> = {
  search_tickets: 'Review customer tickets',
  search_issues: 'Check tracked issues',
  search_product_logs: 'Review service logs',
  list_changes: 'Check recent changes',
  compare_categories: 'Compare categories',
  rank_ticket_contributors: 'Rank ticket reasons',
  build_incident_timeline: 'Build event timeline',
  compare_before_after: 'Compare before and after',
  suggest_runbook_actions: 'Review suggested actions',
}

function EvidenceCitation({ item, id, contradiction = false }: { item?: EvidenceItem; id: string; contradiction?: boolean }) {
  const label = item ? evidenceSourceLabels[item.source] ?? 'Evidence record' : 'Evidence record unavailable'
  return (
    <details className="citation-detail">
      <summary className={`citation${contradiction ? ' contradiction' : ''}`} aria-label={`View ${label}${item ? ` from ${timeLabel(item.observed_at)}` : ''}`}>{contradiction ? `Contrary evidence · ${label}` : label}{item ? ` · ${timeLabel(item.observed_at)}` : ''}</summary>
      <div className="citation-preview">
        <strong>{label}</strong>
        {item && <><small>{evidenceSourceDescriptions[item.source] ?? 'Evidence used in this investigation.'}</small><time>{timeLabel(item.observed_at)}</time><p>{item.summary}</p></>}
        <details className="technical-details"><summary>Technical details</summary><code>{id}</code></details>
      </div>
    </details>
  )
}

function EvidenceList({ ids, evidenceMap, contradiction = false }: { ids: string[]; evidenceMap: Map<string, EvidenceItem>; contradiction?: boolean }) {
  const visible = ids.slice(0, 3)
  const remaining = ids.slice(3)
  if (!ids.length) return null
  return <>
    <div className="citation-list">{visible.map((id) => <EvidenceCitation key={id} id={id} item={evidenceMap.get(id)} contradiction={contradiction} />)}</div>
    {remaining.length > 0 && <details className="more-evidence"><summary>{`View ${remaining.length} more ${contradiction ? 'contrary ' : ''}evidence ${remaining.length === 1 ? 'record' : 'records'}`}</summary><div className="citation-list">{remaining.map((id) => <EvidenceCitation key={id} id={id} item={evidenceMap.get(id)} contradiction={contradiction} />)}</div></details>}
  </>
}

type Investigation = {
  investigation_id: string
  status: string
  progress?: Array<{
    sequence: number
    created_at: string
    type: string
    stage: string
    tool?: string
    message: string
  }>
  report?: {
    status: string
    summary: string
    hypotheses: Array<{
      rank: number
      title: string
      explanation: string
      supporting_evidence: string[]
      contradicting_evidence: string[]
      categories?: string[]
    }>
    category_findings?: Array<{
      category: string
      status: string
      summary: string
      unresolved_questions: string[]
    }>
    evidence: EvidenceItem[]
    unresolved_questions: string[]
    tool_trace: Array<{ tool: string; arguments?: Record<string, unknown>; result?: unknown }>
    recommendations?: Array<{
      category: string
      runbook_id: string | null
      title: string
      next_step: string
      verification: string
      supporting_evidence: string[]
    }>
  } | null
  error?: string | null
}

const scenarioNames: Record<string, string> = {
  payment_issue: 'Payment authorization failure',
  healthy: 'Healthy control',
  unrelated_issue: 'Unrelated issue distractor',
  resolved_issue: 'Resolved payment issue',
}

const categories = ['payments', 'orders', 'account', 'delivery'] as const
type Category = typeof categories[number]

type AlertEpisode = {
  key: string
  category: Category
  alerts: EventRecord[]
  peakAlert: EventRecord
  strength: number
}

function timeLabel(value: string) {
  return new Date(value).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    timeZoneName: 'short',
  })
}

function CountChart({ points, alertWindows, compact = false, centerWindow, selectedWindow }: {
  points: Array<{ index: number; count: number }>
  alertWindows: Set<number>
  compact?: boolean
  centerWindow?: number
  selectedWindow?: number
}) {
  const gradientId = useId().replaceAll(':', '')
  const windowSize = 54
  const centerIndex = centerWindow === undefined ? -1 : points.findIndex((point) => point.index === centerWindow)
  const startIndex = centerIndex < 0
    ? Math.max(0, points.length - windowSize)
    : Math.max(0, Math.min(points.length - windowSize, centerIndex - Math.floor(windowSize / 2)))
  const recent = points.slice(startIndex, startIndex + windowSize)
  const max = Math.max(8, ...recent.map((point) => point.count))
  const width = 760
  const height = 176
  const coordinates = recent.map((point, index) => {
    const x = recent.length <= 1 ? 0 : (index / (recent.length - 1)) * width
    const y = height - (point.count / max) * (height - 16) - 4
    return `${x},${y}`
  }).join(' ')
  const last = recent.at(-1)
  const isCurrentRange = recent.at(-1)?.index === points.at(-1)?.index
  return (
    <div className="chart-wrap">
      <div className="chart-scale"><span>{max}</span><span>{Math.round(max / 2)}</span><span>0</span></div>
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`Ticket counts by 30-minute window${selectedWindow === undefined ? '' : `; selected alert at window ${selectedWindow}`}`}>
        <defs>
          <linearGradient id={`areaFill-${gradientId}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--mint)" stopOpacity=".25" />
            <stop offset="100%" stopColor="var(--mint)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75, 1].map((fraction) => <line key={fraction} x1="0" x2={width} y1={height * fraction} y2={height * fraction} className="gridline" />)}
        {recent.length > 1 && <polygon points={`0,${height} ${coordinates} ${width},${height}`} fill={`url(#areaFill-${gradientId})`} />}
        {recent.length > 1 && <polyline points={coordinates} className="chart-line" />}
        {recent.map((point, index) => alertWindows.has(point.index) && <circle
          key={point.index} cx={recent.length <= 1 ? width : (index / (recent.length - 1)) * width}
          cy={height - (point.count / max) * (height - 16) - 4} r={point.index === selectedWindow ? 6 : compact ? 3 : 4}
          className={`chart-alert-dot${point.index === selectedWindow ? ' selected' : ''}`} />)}
        {last && isCurrentRange && <circle cx={width} cy={height - (last.count / max) * (height - 16) - 4} r="4" className="chart-dot" />}
      </svg>
      <div className="chart-axis"><span>Earlier</span><span>Each point = 30 simulated minutes</span><span>{isCurrentRange ? 'Now' : 'Later'}</span></div>
    </div>
  )
}

function alertStrength(alert: EventRecord) {
  const scores = (alert.payload.scores as Array<{ score: number; threshold: number; anomalous: boolean }> | undefined) ?? []
  return Math.max(0, ...scores.filter((score) => score.anomalous && score.threshold > 0)
    .map((score) => score.score / score.threshold))
}

function buildAlertEpisodes(alerts: EventRecord[]): AlertEpisode[] {
  const episodes: AlertEpisode[] = []
  for (const category of categories) {
    const ordered = alerts.filter((alert) => alert.payload.category === category)
      .slice().sort((a, b) => Number(a.payload.window_index) - Number(b.payload.window_index))
    let current: EventRecord[] = []
    const finish = () => {
      if (!current.length) return
      const peakAlert = current.reduce((peak, alert) => alertStrength(alert) > alertStrength(peak) ? alert : peak)
      episodes.push({ key: `${category}:${String(current[0].payload.window_index)}`, category,
        alerts: current, peakAlert, strength: alertStrength(peakAlert) })
      current = []
    }
    for (const alert of ordered) {
      if (current.length && Number(alert.payload.window_index) !== Number(current[current.length - 1].payload.window_index) + 1) finish()
      current.push(alert)
    }
    finish()
  }
  return episodes.sort((a, b) => Number(a.peakAlert.payload.window_index) - Number(b.peakAlert.payload.window_index))
}

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try {
      return window.localStorage.getItem('signalroom-theme') === 'light' ? 'light' : 'dark'
    } catch {
      return 'dark'
    }
  })
  const [scenario, setScenario] = useState('payment_issue')
  const [focusedCategory, setFocusedCategory] = useState<Category | null>(null)
  const [arrivalModel, setArrivalModel] = useState('poisson')
  const [seed, setSeed] = useState(42)
  const [speed, setSpeed] = useState('normal')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<EventRecord[]>([])
  const [alerts, setAlerts] = useState<EventRecord[]>([])
  const [selectedAlertIds, setSelectedAlertIds] = useState<string[]>([])
  const [selectionTouched, setSelectionTouched] = useState(false)
  const [question, setQuestion] = useState('What is driving this ticket spike?')
  const [investigation, setInvestigation] = useState<Investigation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      'content', theme === 'dark' ? '#101522' : '#f3f6fb',
    )
    try {
      window.localStorage.setItem('signalroom-theme', theme)
    } catch {
      // Theme remains available for this session when browser storage is disabled.
    }
  }, [theme])

  const request = useCallback(async <T,>(url: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || `Request failed (${response.status})`)
    }
    return response.json() as Promise<T>
  }, [])

  const startRun = async () => {
    setError('')
    setBusy(true)
    setEvents([])
    setAlerts([])
    setFocusedCategory(null)
    setSelectedAlertIds([])
    setSelectionTouched(false)
    setInvestigation(null)
    try {
      const next = await request<Run>('/api/runs', {
        method: 'POST', body: JSON.stringify({ scenario, arrival_model: arrivalModel, seed, speed }),
      })
      setRun(next)
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : 'Could not start the simulation.')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!run) return
    const runId = run.run_id
    const stream = new EventSource(`/api/runs/${runId}/stream`)
    stream.onopen = () => setConnected(true)
    stream.onerror = () => setConnected(false)
    stream.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as EventRecord
        setEvents((current) => [...current, event])
        if (event.event_type === 'anomaly_alert') {
          setAlerts((current) => [...current, event])
        }
      } catch {
        setError('The event stream returned an unreadable update.')
      }
    }
    return () => {
      stream.close()
      setConnected(false)
    }
  }, [run?.run_id])

  useEffect(() => {
    if (!run || !['running', 'queued'].includes(run.status)) return
    const runId = run.run_id
    const timer = window.setInterval(() => {
      request<Run>(`/api/runs/${runId}`).then(setRun).catch(() => undefined)
    }, 900)
    return () => window.clearInterval(timer)
  }, [run?.run_id, run?.status, request])

  const observations = useMemo(() => events.filter((event) => event.event_type === 'window_observation'), [events])
  const alertEpisodes = useMemo(() => buildAlertEpisodes(alerts), [alerts])
  const latestScoreEvent = events.filter((event) => event.event_type === 'window_score').at(-1)
  const categoryCards = categories.map((category) => {
    const points = observations.map((event) => ({
      index: Number(event.payload.window_index),
      count: Number((event.payload.category_counts as Record<string, number> | undefined)?.[category] ?? 0),
    }))
    const categoryScore = (latestScoreEvent?.payload.scores_by_category as Record<string, { expected?: number; alerted?: boolean }> | undefined)?.[category]
    const expected = Number(categoryScore?.expected ?? 0)
    const latestCount = points.at(-1)?.count ?? 0
    const categoryEpisodes = alertEpisodes.filter((episode) => episode.category === category)
    const alertWindows = new Set(categoryEpisodes.flatMap((episode) => episode.alerts.map((alert) => Number(alert.payload.window_index))))
    return { category, points, expected, latestCount, alerting: Boolean(categoryScore?.alerted), episodes: categoryEpisodes, alertWindows }
  })
  const defaultEpisode = (focusedCategory ? alertEpisodes.filter((episode) => episode.category === focusedCategory).at(-1) : undefined)
    ?? alertEpisodes.at(-1)
  const effectiveSelectedAlertIds = selectionTouched ? selectedAlertIds : (defaultEpisode ? [defaultEpisode.peakAlert.evidence_id] : [])
  const selectedEpisodes = alertEpisodes.filter((episode) => effectiveSelectedAlertIds.some((id) => episode.alerts.some((alert) => alert.evidence_id === id)))
  const selectedAlert = selectedEpisodes.find((episode) => episode.category === focusedCategory)?.alerts.find((alert) => effectiveSelectedAlertIds.includes(alert.evidence_id))
    ?? selectedEpisodes[0]?.alerts.find((alert) => effectiveSelectedAlertIds.includes(alert.evidence_id))
    ?? (!selectionTouched ? defaultEpisode?.peakAlert : undefined)
  const latestCount = observations.at(-1)?.payload.ticket_count as number | undefined ?? 0
  const baseline = observations.slice(0, Math.min(48, observations.length)).reduce((sum, event) => sum + Number(event.payload.ticket_count ?? 0), 0) /
    Math.max(1, Math.min(48, observations.length))
  const ratio = baseline > 0 ? latestCount / baseline : 0
  const issueEvents = events.filter((event) => event.source === 'issue_tracker')
  const recentTickets = events.filter((event) => event.event_type === 'ticket_created').slice(-5).reverse()

  const controlRun = async (action: 'pause' | 'resume' | 'step' | 'stop') => {
    if (!run) return
    setError('')
    try {
      await request(`/api/runs/${run.run_id}/controls/${action}`, { method: 'POST' })
      setRun(await request<Run>(`/api/runs/${run.run_id}`))
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : 'Could not control the run.')
    }
  }

  const investigate = async () => {
    if (!run || effectiveSelectedAlertIds.length === 0) return
    setError('')
    setInvestigation(null)
    try {
      const started = await request<Investigation>(`/api/runs/${run.run_id}/investigations`, {
        method: 'POST', body: JSON.stringify({ question, alert_evidence_ids: effectiveSelectedAlertIds }),
      })
      setInvestigation(started)
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : 'Could not start investigation.')
    }
  }

  const toggleEpisode = (episode: AlertEpisode) => {
    setInvestigation(null)
    setSelectionTouched(true)
    setFocusedCategory(episode.category)
    setSelectedAlertIds((current) => {
      const episodeIds = episode.alerts.map((alert) => alert.evidence_id)
      const alreadySelected = current.some((id) => episodeIds.includes(id))
      const withoutCategory = current.filter((id) => !alertEpisodes.some((candidate) =>
        candidate.category === episode.category && candidate.alerts.some((alert) => alert.evidence_id === id)))
      return alreadySelected ? withoutCategory : [...withoutCategory, episode.peakAlert.evidence_id]
    })
  }

  const chooseEpisodeWindow = (episode: AlertEpisode, evidenceId: string) => {
    setInvestigation(null)
    setSelectionTouched(true)
    setFocusedCategory(episode.category)
    setSelectedAlertIds((current) => {
      const ids = episode.alerts.map((alert) => alert.evidence_id)
      return current.some((id) => ids.includes(id))
        ? current.map((id) => ids.includes(id) ? evidenceId : id)
        : [...current.filter((id) => !alertEpisodes.some((candidate) => candidate.category === episode.category && candidate.alerts.some((alert) => alert.evidence_id === id))), evidenceId]
    })
  }

  useEffect(() => {
    if (!run || !investigation || !['queued', 'running'].includes(investigation.status)) return
    const runId = run.run_id
    const investigationId = investigation.investigation_id
    const timer = window.setInterval(() => {
      request<Investigation>(`/api/runs/${runId}/investigations/${investigationId}`)
        .then(setInvestigation).catch(() => undefined)
    }, 700)
    return () => window.clearInterval(timer)
  }, [run?.run_id, investigation?.investigation_id, investigation?.status, request])

  const evidenceMap = new Map((investigation?.report?.evidence ?? []).map((item) => [item.evidence_id, item]))
  const runState = run?.status ?? 'ready'
  const runStateLabel = runState === 'running' ? 'Generating events' : runState === 'completed' ? 'Run complete' : runState
  const alertCategory = String(selectedAlert?.payload.category ?? 'all')
  const alertScore = selectedAlert
    ? `${selectedAlert.payload.ticket_count} ${alertCategory} tickets / 30 min · expected ${Number(selectedAlert.payload.expected ?? 0).toFixed(1)}`
    : 'Waiting for alert'
  const detectorScoreFields = (selectedAlert?.payload.signal as { scores?: Record<string, number> } | undefined)?.scores ?? {}
  const progress = investigation?.progress ?? []
  const report = investigation?.report
  const reportFindings = report?.category_findings?.length
    ? report.category_findings
    : report ? selectedEpisodes.map((episode) => ({ category: episode.category, status: 'completed', summary: '', unresolved_questions: [] })) : []
  const findingQuestions = new Set(reportFindings.flatMap((finding) => finding.unresolved_questions))
  const reportQuestions = report?.unresolved_questions.filter((item) => !findingQuestions.has(item)) ?? []
  const completedTools = [...new Set(progress.filter((step) => step.tool && step.type.includes('completed')).map((step) => investigationToolLabels[step.tool!] ?? step.tool!.replaceAll('_', ' ')))]
  const activitySummary = investigation?.status === 'running'
    ? (progress.at(-1)?.message ?? 'Collecting evidence from the selected incident.')
    : completedTools.length ? `Reviewed ${completedTools.join(', ').toLowerCase()}.` : 'Investigation activity is available below.'
  const reportContext = selectedEpisodes.map((episode) => {
    const alert = episode.alerts.find((item) => effectiveSelectedAlertIds.includes(item.evidence_id)) ?? episode.peakAlert
    return `${episode.category} · Window ${String(alert.payload.window_index)} · ${String(alert.payload.ticket_count)} tickets / 30 min`
  })

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><span></span><span></span><span></span></div>
          <div><div className="brand-name">signal<span>room</span></div><div className="brand-caption">OPERATIONS INTELLIGENCE</div></div>
        </div>
        <div className="topbar-right">
          <div className={`connection-pill ${connected ? 'is-connected' : ''}`}><i />{connected ? 'STREAM CONNECTED' : 'LOCAL SIMULATOR'}</div>
          <button className="theme-toggle" type="button" aria-pressed={theme === 'light'}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}>
            <span aria-hidden="true">{theme === 'dark' ? '☼' : '☾'}</span>
            {theme === 'dark' ? 'Light mode' : 'Dark mode'}
          </button>
          <div className="avatar">AR</div>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-label">WORKSPACE</div>
          <button className="nav-item active"><span className="nav-icon">◈</span> Live monitor <span className="nav-count">{alertEpisodes.length}</span></button>
          <button className="nav-item" onClick={() => setError('Metric anomaly monitoring is planned for the next milestone.')}><span className="nav-icon">⌁</span> Metric signals <span className="soon-tag">LATER</span></button>
          <button className="nav-item" onClick={() => setError('Evaluation reports are available through scripts/evaluate_detectors.py.')}><span className="nav-icon">▤</span> Evaluations</button>
          <div className="sidebar-divider" />
          <div className="sidebar-label">SIMULATION</div>
          <div className="simulation-card">
            <div className="sim-card-top"><span className="sim-dot" /> SYNTHETIC ENVIRONMENT</div>
            <p>All activity is generated locally from a reproducible seed.</p>
            <div className="sim-seed"><span>SEED</span><strong>{seed}</strong><span className="seed-lock">⌑</span></div>
          </div>
          <div className="sidebar-foot"><span className="shield">◇</span> Fictional data only</div>
        </aside>

        <section className="main-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> MONITORING <span className="slash">/</span> TICKET SIGNALS</div>
              <h1>Operations monitor <span className="live-label"><i /> LIVE</span></h1>
              <p className="page-subtitle">Watch ticket volume by category. Select an alert to open its incident workspace.</p>
            </div>
            <div className="run-state"><span className={`status-dot ${runState}`} />{runStateLabel}<span className="state-time">{run ? `${observations.length} / 144 windows` : 'No run started'}</span></div>
          </div>

          {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError('')}>Dismiss</button></div>}

          <section className="simulation-toolbar panel">
            <div className="control-heading"><div className="control-icon">▶</div><div><strong>Simulation settings</strong><small>Reproducible synthetic ticket activity</small></div></div>
            <label className="field"><span>Scenario</span><select value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={runState === 'running'}>
              {Object.entries(scenarioNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
            <label className="field compact"><span>Ticket variation</span><select value={arrivalModel} onChange={(event) => setArrivalModel(event.target.value)} disabled={runState === 'running'}>
              <option value="poisson">Baseline</option><option value="negative_binomial">Higher variation</option>
            </select></label>
            <label className="field seed-field"><span>Seed</span><input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} disabled={runState === 'running'} /></label>
            <label className="field speed-field"><span>Playback</span><select value={speed} onChange={(event) => setSpeed(event.target.value)} disabled={runState === 'running'}>
              <option value="slow">Slow</option><option value="normal">Normal</option><option value="fast">Fast</option>
            </select></label>
            <button className="primary-button" onClick={startRun} disabled={busy || runState === 'running'}>{busy ? 'Starting…' : run ? '↻  New run' : '▶  Start run'}</button>
          </section>

          {run && <div className="run-controls">
            <button onClick={() => controlRun('pause')} disabled={run.status !== 'running'}>Ⅱ Pause</button>
            <button onClick={() => controlRun('resume')} disabled={run.status !== 'paused'}>▶ Resume</button>
            <button onClick={() => controlRun('step')} disabled={run.status !== 'paused'}>› Step window</button>
            <button onClick={() => controlRun('stop')} disabled={!['running', 'paused'].includes(run.status)}>■ Stop</button>
            <span className="run-id">RUN <b>{run.run_id.slice(0, 8).toUpperCase()}</b></span>
          </div>}

          <div className="stat-grid">
            <article className="stat-card panel"><div className="stat-label">ALL TICKETS · LAST 30 MIN <span className="stat-icon mint">↗</span></div><div className="stat-value">{latestCount}<span className="stat-unit">tickets</span></div><div className="stat-foot"><span className="mint-text">{observations.length ? `${ratio.toFixed(1)}×` : '—'}</span> vs. initial baseline</div></article>
            <article className="stat-card panel"><div className="stat-label">ALERT EPISODES <span className="stat-icon amber">⌁</span></div><div className="stat-value">{alertEpisodes.length.toString().padStart(2, '0')}</div><div className="stat-foot">Periods of unusual ticket volume</div></article>
            <article className="stat-card panel"><div className="stat-label">INVESTIGATION <span className="stat-icon blue">✳</span></div><div className="stat-value small-value">{investigation?.status === 'running' ? 'In progress' : investigation?.report ? 'Report ready' : '—'}</div><div className="stat-foot">Started by operator · evidence-based</div></article>
            <article className="stat-card panel"><div className="stat-label">COUNTING WINDOW <span className="stat-icon violet">◷</span></div><div className="stat-value">30<span className="stat-unit">min</span></div><div className="stat-foot">Time grouped into each chart point</div></article>
          </div>

          <div className="content-grid">
            <section className="panel chart-panel category-signals-panel">
              <div className="panel-heading"><div><div className="section-kicker">CATEGORY SIGNALS</div><h2>Ticket arrivals by category</h2></div><div className="chart-legend"><i /> Tickets <span>·</span> 30 min</div></div>
              <div className="category-chart-grid">
                {categoryCards.map((card) => <button type="button" key={card.category}
                  className={`category-chart-card ${focusedCategory === card.category ? 'focused' : ''}`}
                  onClick={() => setFocusedCategory(card.category)} aria-pressed={focusedCategory === card.category}>
                  <span className="category-chart-heading"><strong>{card.category}</strong><span className={`category-chart-state${card.alerting ? ' category-alerting' : ''}`}><b>{selectedAlert && alertCategory === card.category ? 'SELECTED' : card.alerting ? 'ALERTING' : 'WITHIN RANGE'}</b><small>{card.episodes.length ? `${card.episodes.length} ${card.episodes.length === 1 ? 'episode' : 'episodes'}` : 'No alerts'}</small></span></span>
                  <span className="category-chart-current">{card.latestCount}<small> tickets · expected {card.expected.toFixed(1)}</small></span>
                  <CountChart points={card.points} alertWindows={card.alertWindows} compact centerWindow={selectedAlert ? Number(selectedAlert.payload.window_index) : undefined} selectedWindow={selectedAlert && alertCategory === card.category ? Number(selectedAlert.payload.window_index) : undefined} />
                </button>)}
              </div>
              <div className="chart-summary"><span><i className="legend-dot normal-dot" /> Typical activity</span><span><i className="legend-dot alert-dot" /> Alert window</span><span className="chart-scale-note">Each category has its own scale</span></div>
              <div className="detector-ribbon"><div className="ribbon-icon">∑</div><div><strong>Ticket-volume checks</strong><small>Compares each category with its usual pattern and flags unusual or sustained increases.</small></div><span className="ribbon-state">{observations.length ? 'MONITORING' : 'WAITING'}</span></div>
            </section>

            <section className="panel timeline-panel">
              <div className="panel-heading"><div><div className="section-kicker">SYSTEM EVENTS</div><h2>Issue timeline</h2></div><span className="record-count">{issueEvents.length} tracked issues</span></div>
              <div className="timeline-list">
                {issueEvents.length === 0 && <div className="empty-timeline"><div className="empty-orbit">◷</div><strong>Waiting for system events</strong><span>The simulation will populate this timeline.</span></div>}
                {issueEvents.slice(-4).reverse().map((event) => (
                  <div className="timeline-item" key={event.evidence_id}><div className={`timeline-marker ${event.event_type === 'issue_resolved' ? 'resolved' : 'issue'}`} /><div className="timeline-content"><div className="timeline-top"><strong>{String(event.payload.issue_key ?? 'Issue')}</strong><time>{timeLabel(event.simulated_at)}</time></div><p>{String(event.payload.title ?? event.event_type)}</p><span className={`issue-status ${String(event.payload.status ?? 'open')}`}>{String(event.payload.status ?? event.event_type).toUpperCase()}</span></div></div>
                ))}
              </div>
              <div className="evidence-source-row"><span className="source-led" /> SYNTHETIC ISSUE TRACKER <span className="source-count">{issueEvents.length}</span></div>
            </section>

            <section className="panel alert-panel">
              <div className="panel-heading"><div><div className="section-kicker">ALERT FEED</div><h2>Ticket-volume alerts</h2><p className="panel-subtitle">Select an episode to inspect it. The strongest window is selected by default.</p></div><span className={`alert-badge ${alerts.length ? 'has-alert' : ''}`}>{alerts.length ? `${alertEpisodes.length} EPISODES` : 'NO ALERTS'}</span></div>
              {alertEpisodes.length === 0 ? <div className="alert-empty"><span className="pulse-ring" />No unusual ticket-volume periods yet. Each category is monitored separately.</div> : <div className="episode-list">
                {categories.map((category) => {
                  const episodes = alertEpisodes.filter((episode) => episode.category === category)
                  if (!episodes.length) return null
                  return <div className="episode-category" key={category}><div className="episode-category-heading"><strong>{category}</strong><span>{episodes.length} {episodes.length === 1 ? 'episode' : 'episodes'}</span></div>
                    {episodes.map((episode) => {
                      const selectedId = effectiveSelectedAlertIds.find((id) => episode.alerts.some((alert) => alert.evidence_id === id))
                      const chosen = episode.alerts.find((alert) => alert.evidence_id === selectedId) ?? episode.peakAlert
                      return <div className={`episode-row ${selectedId ? 'selected' : ''}`} key={episode.key}>
                        <label className="episode-select"><input type="checkbox" checked={Boolean(selectedId)} onChange={() => toggleEpisode(episode)} />
                          <span><strong>W{String(episode.alerts[0].payload.window_index)}–W{String(episode.alerts.at(-1)?.payload.window_index)}</strong><small>{episode.alerts.length} alert {episode.alerts.length === 1 ? 'window' : 'windows'} · peak {String(episode.peakAlert.payload.ticket_count)} tickets</small></span>
                        </label>
                        <label className="episode-window">Window<select aria-label={`Selected ${category} alert window`} disabled={!selectedId} value={chosen.evidence_id} onChange={(event) => chooseEpisodeWindow(episode, event.target.value)}>{episode.alerts.map((alert) => <option key={alert.evidence_id} value={alert.evidence_id}>W{String(alert.payload.window_index)} · {String(alert.payload.ticket_count)} tickets</option>)}</select></label>
                      </div>
                    })}
                  </div>
                })}
              </div>}
            </section>

            <section className="panel investigator-panel">
              <div className="panel-heading"><div><div className="section-kicker">SELECTED INCIDENT</div><h2>Incident workspace</h2></div><div className="agent-orb">✳</div></div>
              {selectedAlert ? <div className="workspace-signal"><span>Selected signal</span><strong>{alertCategory} · Window {String(selectedAlert.payload.window_index)}</strong><p>{alertScore}</p><details className="detection-details"><summary>Detection details</summary><div className="detector-tags">{(selectedAlert.payload.detectors_triggered as string[] ?? []).map((detector) => <span key={detector}>{detector.replaceAll('_', ' ')}</span>)}</div><div className="detector-values">{Object.entries(detectorScoreFields).map(([name, value]) => <span key={name}>{name.replaceAll('_', ' ')} <b>{Number(value).toFixed(2)}</b></span>)}</div></details></div> : <div className="workspace-empty">Select an alert episode to review its evidence and investigate the likely cause.</div>}
              <div className="investigator-prompt"><div className="prompt-avatar">AR</div><div><strong>Operator question</strong><small>Investigate {effectiveSelectedAlertIds.length || 'the selected'} alert {effectiveSelectedAlertIds.length === 1 ? 'episode' : 'episodes'} using available evidence.</small></div></div>
              <div className="ask-row"><input value={question} onChange={(event) => setQuestion(event.target.value)} disabled={!effectiveSelectedAlertIds.length || investigation?.status === 'running'} aria-label="Question for investigator" /><button className="ask-button" onClick={investigate} disabled={!effectiveSelectedAlertIds.length || !question.trim() || investigation?.status === 'running'}>{investigation?.status === 'running' ? 'Investigating…' : 'Investigate'}</button></div>
              {!effectiveSelectedAlertIds.length && <div className="investigator-hint">Select one or more alert episodes to start an investigation.</div>}
              {progress.length > 0 && <details className="investigation-progress" open={investigation?.status === 'running'} aria-live="polite"><summary className="progress-heading"><strong>{investigation?.status === 'running' ? 'Investigation in progress' : 'Investigation activity'}</strong><span>{progress.length} updates</span></summary><p className="activity-summary">{activitySummary}</p><div className="progress-detail-list">{progress.filter((step) => !step.tool || !step.type.includes('started')).map((step) => <div className={`progress-step ${step.type}`} key={step.sequence}><i />{step.tool ? <><strong>{investigationToolLabels[step.tool] ?? step.tool.replaceAll('_', ' ')}</strong><span>{step.type.includes('completed') ? step.message : 'Checking evidence…'}</span></> : <span>{step.message}</span>}</div>)}</div></details>}
              {investigation?.status === 'failed' && <div className="investigation-error">{investigation.error || 'Investigation failed. Check API key or select offline demo mode.'}</div>}
            </section>
          </div>

          {report && <section className="panel rca-report">
            <div className="report-heading"><div><div className="section-kicker">INVESTIGATION REPORT</div><h2>What we found</h2></div><span className={`report-status ${report.status}`}>{report.status === 'completed' ? 'LIKELY EXPLANATION' : report.status === 'incomplete' ? 'PARTIAL FINDING' : 'NOT ENOUGH EVIDENCE'}</span></div>
            <div className="report-context">{reportContext.map((item) => <span key={item}>{item}</span>)}</div>
            {(reportFindings.length > 1 || report.hypotheses.length === 0) && <p className="report-summary">{report.summary}</p>}
            <div className="report-findings">{reportFindings.map((finding) => {
              const hypotheses = report.hypotheses.filter((hypothesis) => (hypothesis.categories ?? [selectedEpisodes[0]?.category]).includes(finding.category) && (hypothesis.categories?.length ?? 0) <= 1)
              const showFindingSummary = finding.summary && (reportFindings.length > 1 || hypotheses.length === 0)
              const uniqueQuestions = finding.unresolved_questions.filter((item) => !report.unresolved_questions.includes(item))
              return <section className="category-finding" key={finding.category}>
                <div className="category-finding-heading"><h3>{finding.category}</h3><span className={`finding-status ${finding.status}`}>{finding.status === 'completed' ? 'REVIEWED' : finding.status.replaceAll('_', ' ').toUpperCase()}</span></div>
                {showFindingSummary && <p>{finding.summary}</p>}
                {hypotheses.map((hypothesis) => <article className="hypothesis" key={`${finding.category}-${hypothesis.rank}-${hypothesis.title}`}>
                  <div className="hypothesis-rank">{hypothesis.rank === 1 ? 'LEAD' : `#${hypothesis.rank}`}</div><div className="hypothesis-main"><h3>{hypothesis.title}</h3><p>{hypothesis.explanation}</p>
                    <div className="report-evidence-group"><span>Supporting evidence</span><EvidenceList ids={hypothesis.supporting_evidence} evidenceMap={evidenceMap} /></div>
                    {hypothesis.contradicting_evidence.length > 0 && <div className="report-evidence-group contrary-group"><span>Evidence that does not fit</span><EvidenceList ids={hypothesis.contradicting_evidence} evidenceMap={evidenceMap} contradiction /> </div>}
                  </div>
                </article>)}
                {uniqueQuestions.map((item) => <div className="unresolved" key={item}><strong>What remains unknown</strong><p>{item}</p></div>)}
              </section>
            })}</div>
            {report.hypotheses.filter((hypothesis) => (hypothesis.categories?.length ?? 0) > 1).length > 0 && <section className="shared-findings"><h3>Possible shared causes</h3>{report.hypotheses.filter((hypothesis) => (hypothesis.categories?.length ?? 0) > 1).map((hypothesis) => <article className="hypothesis" key={`shared-${hypothesis.rank}-${hypothesis.title}`}><div className="hypothesis-main"><h3>{hypothesis.title}</h3><p>{hypothesis.explanation}</p><small>Affects: {hypothesis.categories?.join(', ')}</small><div className="report-evidence-group"><span>Supporting evidence</span><EvidenceList ids={hypothesis.supporting_evidence} evidenceMap={evidenceMap} /></div></div></article>)}</section>}
            {reportQuestions.length > 0 && <section className="unresolved report-unknown"><strong>What remains unknown</strong>{reportQuestions.map((item) => <p key={item}>{item}</p>)}</section>}
            {(report.recommendations ?? []).length > 0 && <section className="recommendations"><div className="recommendation-heading"><h3>Recommended next step</h3><span>ADVISORY ONLY</span></div>{(report.recommendations ?? []).map((item) => <article className="recommendation" key={`${item.category}-${item.runbook_id ?? item.title}`}><strong>{item.title}</strong><p>{item.next_step}</p><small><b>Verify:</b> {item.verification}</small>{item.supporting_evidence.length > 0 && <div className="report-evidence-group"><span>Based on</span><EvidenceList ids={item.supporting_evidence} evidenceMap={evidenceMap} /></div>}</article>)}</section>}
            <details className="evidence-details"><summary>All evidence records · {report.evidence.length}</summary><div>{report.evidence.map((item) => <article key={item.evidence_id}><strong>{evidenceSourceLabels[item.source] ?? 'Evidence record'} · {timeLabel(item.observed_at)}</strong><small>{evidenceSourceDescriptions[item.source] ?? 'Evidence used in this investigation.'}</small><p>{item.summary}</p><details className="technical-details"><summary>Technical details</summary><code>{item.evidence_id}</code></details></article>)}</div></details>
            <details className="trace-details"><summary>Technical investigation trace · {report.tool_trace.length} results</summary><div className="trace-list">{report.tool_trace.map((item, index) => <div key={`${item.tool}-${index}`}><code>{item.tool}</code><pre>{JSON.stringify(item.result, null, 2)}</pre></div>)}</div></details>
          </section>}

          <section className="panel feed-panel">
            <div className="panel-heading"><div><div className="section-kicker">RECENT CUSTOMER MESSAGES</div><h2>Latest support tickets</h2></div><span className="record-count">{recentTickets.length} shown</span></div>
            <p className="panel-subtitle">Each message is one ticket. These messages are synthetic; customers who did not contact support are not shown.</p>
            {recentTickets.length === 0 ? <div className="feed-empty">New synthetic tickets will appear as the event stream advances.</div> : <div className="ticket-table"><div className="ticket-header"><span>TICKET</span><span>CUSTOMER MESSAGE</span><span>CHANNEL</span><span>TIME</span></div>{recentTickets.map((ticket) => <div className="ticket-row" key={ticket.evidence_id}><span className="ticket-key">{String(ticket.payload.ticket_key)}</span><span className="ticket-summary"><strong><i className={`category-dot ${ticket.payload.category === 'payments' ? 'payment' : ''}`} />{String(ticket.payload.title)}</strong><small>{String(ticket.payload.body ?? '')}</small><details><summary>Read full message</summary><p>{String(ticket.payload.body ?? '')}</p></details><em>{String(ticket.payload.reason ?? 'support contact').replaceAll('_', ' ')}</em></span><span className="ticket-channel">{String(ticket.payload.channel)}</span><time>{timeLabel(ticket.simulated_at)}</time></div>)}</div>}
          </section>

          <footer className="footer-note"><span>◈</span> Local synthetic environment <i /> Statistical scores are diagnostic evidence, not proof of causality.</footer>
        </section>
      </div>
    </main>
  )
}

export default App
