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
type Report = {
  status: string
  summary: string
  investigator_mode?: string
  hypotheses: Array<{ rank: number; title: string; explanation: string; supporting_evidence: string[]; contradicting_evidence: string[] }>
  evidence: Evidence[]
  unresolved_questions: string[]
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
type Health = { investigator_mode: string }

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
  const [scenario, setScenario] = useState('payment_issue')
  const [arrivalModel, setArrivalModel] = useState('poisson')
  const [seed, setSeed] = useState(42)
  const [speed, setSpeed] = useState('normal')
  const [mode, setMode] = useState<'automatic' | 'manual'>('automatic')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('signalroom-theme') === 'light' ? 'light' : 'dark' } catch { return 'dark' }
  })
  const [busy, setBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const lastSequence = useRef(0)
  const runRef = useRef<Run | null>(null)
  const eventsRef = useRef<StreamEvent[]>([])

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
      restored.push(...page)
      if (page.length < 2000) break
      cursor = page[page.length - 1].sequence
    }
    lastSequence.current = restored.at(-1)?.sequence ?? 0
    setRun(savedRun)
    setEvents(restored)
    setMode(savedRun.config.investigation_mode === 'manual' ? 'manual' : 'automatic')
    setScenario(savedRun.scenario)
    setSeed(savedRun.seed)
    setArrivalModel(savedRun.arrival_model)
    setError('')
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      jsonRequest<Run[]>('/api/runs?limit=20'),
      jsonRequest<Health>('/api/health'),
    ]).then(async ([recent, currentHealth]) => {
      if (!active) return
      setRuns(recent)
      setHealth(currentHealth)
      const requestedRun = new URLSearchParams(window.location.search).get('run')
      const initial = recent.find((item) => item.run_id === requestedRun) ?? recent[0]
      if (initial) await loadRun(initial.run_id)
    }).catch((issue) => {
      if (active) setError(issue instanceof Error ? issue.message : 'Could not restore the latest replay.')
    })
    return () => { active = false }
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
        setEvents((current) => [...current, event])
      } catch { setError('The event stream returned an unreadable update.') }
    }
    return () => { stream.close(); setConnected(false) }
  }, [run?.run_id])

  useEffect(() => {
    if (!run || !['running', 'queued', 'paused', 'stopping'].includes(run.status)) return
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
  const latestScore = events.filter((item) => item.event_type === 'window_score').at(-1)
  const counts = (latestObservation?.payload.category_counts ?? {}) as Record<string, number>
  const scores = (latestScore?.payload.scores_by_category ?? {}) as Record<string, { expected?: number; alerted?: boolean }>
  const alertEvents = useMemo(() => events.filter((item) => item.event_type === 'anomaly_alert'), [events])
  const currentRunMode = run?.config.investigation_mode === 'manual' ? 'manual' : 'automatic'
  const statusLabel = !run ? 'Ready to replay' : run.status === 'running' ? 'Running' : run.status === 'completed' ? 'Replay complete' : run.status

  async function startReplay() {
    if (busy) return
    setBusy(true); setError(''); setEvents([]); setIncidents([]); lastSequence.current = 0
    try {
      const next = await jsonRequest<Run>('/api/runs', {
        method: 'POST', body: JSON.stringify({ scenario, arrival_model: arrivalModel, seed, speed, investigation_mode: mode }),
      })
      setRun(next)
      setRuns((existing) => [next, ...existing.filter((item) => item.run_id !== next.run_id)].slice(0, 20))
      const url = new URL(window.location.href); url.search = ''; url.hash = ''
      window.history.replaceState(null, '', url)
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not start the replay.') }
    finally { setBusy(false) }
  }

  async function controlReplay(action: 'pause' | 'resume' | 'step' | 'stop') {
    if (!run) return
    try {
      await jsonRequest(`/api/runs/${encodeURIComponent(run.run_id)}/controls/${action}`, { method: 'POST' })
      setRun(await jsonRequest<Run>(`/api/runs/${encodeURIComponent(run.run_id)}`))
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not control the replay.') }
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
        <div className="replay-status"><span className={`status-dot ${run?.status ?? 'ready'}`} />Synthetic replay · {statusLabel}<small>{run ? `${observations.length} windows completed` : 'No replay started'}</small></div></div>
      {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError('')}>Dismiss</button></div>}

      <details className="replay-setup" open={!run}>
        <summary>{run ? 'Replay settings' : 'Start a synthetic replay'}</summary>
        <div className="replay-fields">
          <label className="field"><span>Scenario</span><select value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={run?.status === 'running'}>{Object.entries(scenarioNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="field"><span>Ticket variation</span><select value={arrivalModel} onChange={(event) => setArrivalModel(event.target.value)}><option value="poisson">Baseline</option><option value="negative_binomial">Higher variation</option></select></label>
          <label className="field"><span>Seed</span><input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
          <label className="field"><span>Playback</span><select value={speed} onChange={(event) => setSpeed(event.target.value)}><option value="slow">Slow</option><option value="normal">Normal</option><option value="fast">Fast</option></select></label>
          <label className="field"><span>Investigation</span><select value={mode} onChange={(event) => setMode(event.target.value as 'automatic' | 'manual')}><option value="automatic">Automatic</option><option value="manual">Manual</option></select></label>
          <button className="primary-button" onClick={() => void startReplay()} disabled={busy || run?.status === 'running'}>{busy ? 'Starting…' : run ? 'Start new replay' : 'Start replay'}</button>
        </div>
        <p className="replay-note">{mode === 'automatic' ? `Automatic investigations use ${health?.investigator_mode === 'offline_demo' ? 'the offline demo investigator' : 'the configured investigator'}; OpenAI investigations may use credits.` : 'Manual mode records alerts and waits for you to start each investigation.'}</p>
      </details>

      {run && <div className="replay-toolbar"><span>Run <code>{run.run_id.slice(0, 8).toUpperCase()}</code> · {scenarioNames[run.scenario] ?? run.scenario} · seed {run.seed} · {currentRunMode} investigation</span>
        <div>{run.status === 'running' && <button onClick={() => void controlReplay('pause')}>Pause</button>}{run.status === 'paused' && <><button onClick={() => void controlReplay('resume')}>Resume</button><button onClick={() => void controlReplay('step')}>Step window</button></>}{['running', 'paused'].includes(run.status) && <button onClick={() => void controlReplay('stop')}>Stop</button>}</div></div>}

      <section className="counts-panel panel" aria-label="Latest category ticket counts">
        <div className="counts-heading"><div><div className="section-kicker">TICKET COUNTS — LAST 30 MINUTES</div><h2>{latestObservation ? `Last window completed: ${formatTime(latestObservation.payload.window_end)}` : 'Waiting for the first completed window'}</h2></div><span>{run ? `${observations.length} windows` : 'No replay'}</span></div>
        <div className="count-table"><div className="count-row count-header"><span>Category</span><span>Tickets</span><span>Usual</span><span>Status</span></div>
          {categoryNames.map((category) => {
            const raised = scores[category]?.alerted === true
            const expected = scores[category]?.expected
            return <div className={`count-row ${raised ? 'count-alert' : ''}`} key={category}><strong>{category}</strong><b>{latestObservation ? Number(counts[category] ?? 0) : '—'}</b><span>{expected == null || !Number.isFinite(Number(expected)) || Number(expected) <= 0 ? 'warming up' : Number(expected).toFixed(1)}</span><span className={raised ? 'count-status-alert' : 'count-status-normal'}>{!latestObservation ? 'Waiting' : raised ? 'ALERT' : 'Normal'}</span></div>
          })}
        </div>
      </section>

      <section className="incident-stream" aria-label="Alert and investigation updates">
        <div className="section-kicker">ALERTS AND INVESTIGATIONS</div>
        {!run && <p className="stream-empty">Start a replay to watch ticket counts and automatic investigations.</p>}
        {run && incidents.length === 0 && <p className="stream-empty">No alert episodes yet. The detector is watching each category.</p>}
        {incidents.map(({ incident, latestJob, lastReport }) => {
          const totals = episodeNumbers(incident)
          const report = lastReport?.report
          const evidenceMap = new Map((report?.evidence ?? []).map((item) => [item.evidence_id, item]))
          return <article className={`incident-card ${incident.status === 'active' ? 'is-alerting' : 'is-ended'}`} key={incident.incident_id}>
            <header className="incident-card-heading"><div><div className="section-kicker">{incident.status === 'active' ? 'NEW ALERT' : 'ALERT EPISODE ENDED'} — {incident.category.toUpperCase()}</div>
              <h2>Episode began {formatTime(incident.opened_at)}</h2><p>{totals.windows} alert {totals.windows === 1 ? 'window' : 'windows'} · {totals.tickets} tickets vs {totals.expected.toFixed(1)} expected</p></div>
              <span className={`incident-state ${incident.status}`}>{incident.status === 'active' ? 'Alerting' : 'Ended'}</span></header>
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

      {run && <details className="replay-history"><summary>Earlier replays ({runs.filter((item) => item.run_id !== run.run_id).length})</summary>{runs.filter((item) => item.run_id !== run.run_id).map((item) => <button key={item.run_id} onClick={() => void setReplay(item.run_id)} disabled={busy}><strong>{scenarioNames[item.scenario] ?? item.scenario}</strong><span>{formatTime(item.created_at)} · {item.status} · seed {item.seed}</span></button>)}</details>}
      <footer className="monitor-footer">Synthetic data only · Ticket counts trigger alerts; evidence supports or limits the explanation.</footer>
    </section></div>
  </main>
}
