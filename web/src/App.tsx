import { useCallback, useEffect, useMemo, useState } from 'react'

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
    }>
    evidence: Array<{ evidence_id: string; source: string; observed_at: string; summary: string }>
    unresolved_questions: string[]
    tool_trace: Array<{ tool: string; arguments?: Record<string, unknown>; result?: unknown }>
  } | null
  error?: string | null
}

const scenarioNames: Record<string, string> = {
  payment_issue: 'Payment authorization failure',
  healthy: 'Healthy control',
  unrelated_issue: 'Unrelated issue distractor',
  resolved_issue: 'Resolved payment issue',
}

function timeLabel(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function CountChart({ points, category }: { points: Array<{ index: number; count: number }>; category: string }) {
  const recent = points.slice(-54)
  const max = Math.max(8, ...recent.map((point) => point.count))
  const width = 760
  const height = 176
  const coordinates = recent.map((point, index) => {
    const x = recent.length <= 1 ? 0 : (index / (recent.length - 1)) * width
    const y = height - (point.count / max) * (height - 16) - 4
    return `${x},${y}`
  }).join(' ')
  const last = recent.at(-1)
  return (
    <div className="chart-wrap">
      <div className="chart-scale"><span>{max}</span><span>{Math.round(max / 2)}</span><span>0</span></div>
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${category === 'all' ? 'All category' : category} ticket counts by 30-minute window`}>
        <defs>
          <linearGradient id="areaFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--mint)" stopOpacity=".25" />
            <stop offset="100%" stopColor="var(--mint)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75, 1].map((fraction) => <line key={fraction} x1="0" x2={width} y1={height * fraction} y2={height * fraction} className="gridline" />)}
        {recent.length > 1 && <polygon points={`0,${height} ${coordinates} ${width},${height}`} fill="url(#areaFill)" />}
        {recent.length > 1 && <polyline points={coordinates} className="chart-line" />}
        {last && <circle cx={width} cy={height - (last.count / max) * (height - 16) - 4} r="4" className="chart-dot" />}
      </svg>
      <div className="chart-axis"><span>Earlier</span><span>Each point = 30 simulated minutes</span><span>Now</span></div>
    </div>
  )
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
  const [signalCategory, setSignalCategory] = useState('all')
  const [arrivalModel, setArrivalModel] = useState('poisson')
  const [seed, setSeed] = useState(42)
  const [speed, setSpeed] = useState('normal')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<EventRecord[]>([])
  const [alerts, setAlerts] = useState<EventRecord[]>([])
  const [selectedAlert, setSelectedAlert] = useState<EventRecord | null>(null)
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
    setSelectedAlert(null)
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
          setSelectedAlert(event)
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
  const points = observations.map((event) => ({
    index: Number(event.payload.window_index),
    count: signalCategory === 'all'
      ? Number(event.payload.ticket_count)
      : Number((event.payload.category_counts as Record<string, number> | undefined)?.[signalCategory] ?? 0),
  }))
  const latestCount = points.at(-1)?.count ?? 0
  const baseline = points.slice(0, Math.min(48, points.length)).reduce((sum, point) => sum + point.count, 0) /
    Math.max(1, Math.min(48, points.length))
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
    if (!run || !selectedAlert) return
    setError('')
    setInvestigation(null)
    try {
      const started = await request<Investigation>(`/api/runs/${run.run_id}/investigations`, {
        method: 'POST', body: JSON.stringify({ question, alert_evidence_id: selectedAlert.evidence_id }),
      })
      setInvestigation(started)
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : 'Could not start investigation.')
    }
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
          <button className="nav-item active"><span className="nav-icon">◈</span> Incident room <span className="nav-count">{alerts.length}</span></button>
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
              <div className="eyebrow"><span className="eyebrow-line" /> INCIDENT OPERATIONS <span className="slash">/</span> LIVE ROOM</div>
              <h1>Incident room <span className="live-label"><i /> LIVE</span></h1>
              <p className="page-subtitle">Follow the signal. Ask the system. See what the evidence supports.</p>
            </div>
            <div className="run-state"><span className={`status-dot ${runState}`} />{runStateLabel}<span className="state-time">{run ? `${points.length} / 144 windows` : 'No run started'}</span></div>
          </div>

          {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError('')}>Dismiss</button></div>}

          <section className="control-strip panel">
            <div className="control-heading"><div className="control-icon">▶</div><div><strong>Scenario controls</strong><small>Seeded, stochastic event stream</small></div></div>
            <label className="field"><span>Scenario</span><select value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={runState === 'running'}>
              {Object.entries(scenarioNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
            <label className="field compact"><span>Arrival model</span><select value={arrivalModel} onChange={(event) => setArrivalModel(event.target.value)} disabled={runState === 'running'}>
              <option value="poisson">Poisson</option><option value="negative_binomial">Negative binomial</option>
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
            <article className="stat-card panel"><div className="stat-label">{signalCategory === 'all' ? 'ALL' : signalCategory.toUpperCase()} TICKETS · LAST WINDOW <span className="stat-icon mint">↗</span></div><div className="stat-value">{latestCount}<span className="stat-unit">tickets</span></div><div className="stat-foot"><span className="mint-text">{points.length ? `${ratio.toFixed(1)}×` : '—'}</span> vs. initial baseline</div></article>
            <article className="stat-card panel"><div className="stat-label">ALERT EVENTS <span className="stat-icon amber">⌁</span></div><div className="stat-value">{alerts.length.toString().padStart(2, '0')}</div><div className="stat-foot">Detector alerts emitted in this run</div></article>
            <article className="stat-card panel"><div className="stat-label">INVESTIGATION <span className="stat-icon blue">✳</span></div><div className="stat-value small-value">{investigation?.status ?? '—'}</div><div className="stat-foot">Operator-triggered · evidence-linked</div></article>
            <article className="stat-card panel"><div className="stat-label">WINDOW SIZE <span className="stat-icon violet">◷</span></div><div className="stat-value">30<span className="stat-unit">min</span></div><div className="stat-foot">Ticket count scoring interval</div></article>
          </div>

          <div className="content-grid">
            <section className="panel chart-panel">
              <div className="panel-heading"><div><div className="section-kicker">SIGNAL OVERVIEW</div><h2>Support ticket arrivals</h2></div><div className="chart-tools"><label className="signal-category-filter"><span>Category</span><select value={signalCategory} onChange={(event) => setSignalCategory(event.target.value)}><option value="all">All tickets</option><option value="payments">Payments</option><option value="orders">Orders</option><option value="account">Account</option><option value="delivery">Delivery</option></select></label><div className="chart-legend"><i /> Tickets <span>·</span> 30 min</div></div></div>
              <CountChart points={points} category={signalCategory} />
              <div className="chart-summary"><span><i className="legend-dot normal-dot" /> Normal baseline</span><span><i className="legend-dot alert-dot" /> Incident window</span><span className="chart-model">Model: {arrivalModel === 'poisson' ? 'Poisson' : 'Negative binomial'}</span></div>
              <div className="detector-ribbon"><div className="ribbon-icon">∑</div><div><strong>Category detector ensemble</strong><small>Per-category Poisson & NB likelihood · Z-score · CUSUM · Gamma-Poisson BF</small></div><span className="ribbon-state">{points.length ? 'SCORING' : 'IDLE'}</span></div>
            </section>

            <section className="panel timeline-panel">
              <div className="panel-heading"><div><div className="section-kicker">OBSERVED EVIDENCE</div><h2>System timeline</h2></div><span className="record-count">{issueEvents.length} issue events</span></div>
              <div className="timeline-list">
                {issueEvents.length === 0 && <div className="empty-timeline"><div className="empty-orbit">◷</div><strong>Waiting for system events</strong><span>The simulation will populate this timeline.</span></div>}
                {issueEvents.slice(-4).reverse().map((event) => (
                  <div className="timeline-item" key={event.evidence_id}><div className={`timeline-marker ${event.event_type === 'issue_resolved' ? 'resolved' : 'issue'}`} /><div className="timeline-content"><div className="timeline-top"><strong>{String(event.payload.issue_key ?? 'Issue')}</strong><time>{timeLabel(event.simulated_at)}</time></div><p>{String(event.payload.title ?? event.event_type)}</p><span className={`issue-status ${String(event.payload.status ?? 'open')}`}>{String(event.payload.status ?? event.event_type).toUpperCase()}</span></div></div>
                ))}
              </div>
              <div className="evidence-source-row"><span className="source-led" /> FICTITIOUS ISSUE TRACKER <span className="source-count">{issueEvents.length}</span></div>
            </section>

            <section className="panel alert-panel">
              <div className="panel-heading"><div><div className="section-kicker">DETECTION OUTPUT</div><h2>Active alert</h2></div><span className={`alert-badge ${selectedAlert ? 'has-alert' : ''}`}>{selectedAlert ? 'NEEDS REVIEW' : 'NO ALERT'}</span></div>
              {selectedAlert ? <><div className="alert-body"><div className="alert-mark">!</div><div className="alert-copy"><strong>{alertCategory === 'all' ? 'Ticket volume increased' : `${alertCategory} ticket volume increased`}</strong><p>{alertScore} · window {String(selectedAlert.payload.window_index)}</p><div className="detector-tags">{(selectedAlert.payload.detectors_triggered as string[] ?? []).map((detector) => <span key={detector}>{detector.replaceAll('_', ' ')}</span>)}</div><div className="detector-values">{Object.entries(detectorScoreFields).map(([name, value]) => <span key={name}>{name.replaceAll('_', ' ')} <b>{Number(value).toFixed(2)}</b></span>)}</div></div></div>
                {alerts.length > 1 && <div className="alert-history" aria-label="Recent category alerts">{alerts.slice(-6).reverse().map((alert) => <button type="button" key={alert.evidence_id} className={selectedAlert.evidence_id === alert.evidence_id ? 'selected' : ''} onClick={() => setSelectedAlert(alert)}>{String(alert.payload.category)} · W{String(alert.payload.window_index)}</button>)}</div>}
              </>
                : <div className="alert-empty"><span className="pulse-ring" />No threshold crossing yet. The independent detector is evaluating each window.</div>}
            </section>

            <section className="panel investigator-panel">
              <div className="panel-heading"><div><div className="section-kicker">OPERATOR-TRIGGERED RCA</div><h2>Ask the investigator</h2></div><div className="agent-orb">✳</div></div>
              <div className="investigator-prompt"><div className="prompt-avatar">AR</div><div><strong>Operator question</strong><small>Investigate the selected alert using available evidence.</small></div></div>
              <div className="ask-row"><input value={question} onChange={(event) => setQuestion(event.target.value)} disabled={!selectedAlert || investigation?.status === 'running'} aria-label="Question for investigator" /><button className="ask-button" onClick={investigate} disabled={!selectedAlert || !question.trim() || investigation?.status === 'running'}>{investigation?.status === 'running' ? 'Investigating…' : 'Investigate  ↗'}</button></div>
              {!selectedAlert && <div className="investigator-hint">An alert will appear here when a detector threshold is crossed.</div>}
              {investigation?.progress && investigation.progress.length > 0 && <div className="investigation-progress" aria-live="polite"><div className="progress-heading"><strong>{investigation.status === 'running' ? 'Investigation in progress' : 'Investigation activity'}</strong><span>{investigation.progress.length} steps</span></div>{investigation.progress.map((step) => <div className={`progress-step ${step.type}`} key={step.sequence}><i />{step.tool ? <><code>{step.tool}</code><span>{step.message}</span></> : <span>{step.message}</span>}</div>)}</div>}
              {investigation?.status === 'failed' && <div className="investigation-error">{investigation.error || 'Investigation failed. Check API key or select offline demo mode.'}</div>}
              {investigation?.report && <div className="report-content">
                <div className={`report-status ${investigation.report.status}`}>{investigation.report.status === 'completed' ? 'EVIDENCE REVIEW COMPLETE' : investigation.report.status === 'incomplete' ? 'PARTIAL INVESTIGATION' : 'INSUFFICIENT EVIDENCE'}</div>
                <p className="report-summary">{investigation.report.summary}</p>
                {investigation.report.hypotheses.map((hypothesis) => <article className="hypothesis" key={`${hypothesis.rank}-${hypothesis.title}`}>
                  <div className="hypothesis-rank">0{hypothesis.rank}</div><div className="hypothesis-main"><strong>{hypothesis.title}</strong><p>{hypothesis.explanation}</p>
                    <div className="citation-list">{hypothesis.supporting_evidence.map((id) => { const item = evidenceMap.get(id); return <span className="citation" key={id} title={`${item?.source ?? 'Evidence'} · ${item?.observed_at ?? ''}`}>↳ {item?.source ?? 'tool evidence'} · {id.slice(0, 8)}</span> })}</div>
                    {hypothesis.contradicting_evidence.map((id) => <span className="citation contradiction" key={id}>↳ contrary evidence · {id.slice(0, 8)}</span>)}
                  </div>
                </article>)}
                {investigation.report.unresolved_questions.length > 0 && <div className="unresolved"><strong>Still unknown</strong>{investigation.report.unresolved_questions.map((item) => <p key={item}>{item}</p>)}</div>}
                <details className="trace-details"><summary>Investigation trace · {investigation.report.tool_trace.length} tool results</summary><div className="trace-list">{investigation.report.tool_trace.map((item, index) => <div key={`${item.tool}-${index}`}><code>{item.tool}</code><pre>{JSON.stringify(item.result, null, 2)}</pre></div>)}</div></details>
              </div>}
            </section>
          </div>

          <section className="panel feed-panel">
            <div className="panel-heading"><div><div className="section-kicker">RECENT CUSTOMER SIGNAL</div><h2>Latest support tickets</h2></div><span className="record-count">{recentTickets.length} shown</span></div>
            {recentTickets.length === 0 ? <div className="feed-empty">New synthetic tickets will appear as the event stream advances.</div> : <div className="ticket-table"><div className="ticket-header"><span>TICKET</span><span>SUMMARY</span><span>CHANNEL</span><span>TIME</span></div>{recentTickets.map((ticket) => <div className="ticket-row" key={ticket.evidence_id}><span className="ticket-key">{String(ticket.payload.ticket_key)}</span><span><i className={`category-dot ${ticket.payload.category === 'payments' ? 'payment' : ''}`} />{String(ticket.payload.title)}</span><span className="ticket-channel">{String(ticket.payload.channel)}</span><time>{timeLabel(ticket.simulated_at)}</time></div>)}</div>}
          </section>

          <footer className="footer-note"><span>◈</span> Local synthetic environment <i /> Statistical scores are diagnostic evidence, not proof of causality.</footer>
        </section>
      </div>
    </main>
  )
}

export default App
