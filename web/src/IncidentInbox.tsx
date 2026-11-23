import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type Incident = {
  incident_id: string; stream_id: string; category: string; status: string; operational_status: string
  opened_at: string; latest_window_end: string; episode: Record<string, any>
  statistics: {alert_windows: number; tickets: number; expected: number; peak_window_tickets?: number}
  latest_investigation?: Pick<Investigation, 'job_id' | 'reason' | 'cutoff' | 'status' | 'created_at' | 'completed_at'> | null
}
type ReplayRun = { run_id: string; created_at: string; scenario: string; seed: number; status: string }
type Notification = {
  incident_id: string; level: 'immediate' | 'review_soon' | 'monitor'; rationale: string
  operational_status?: string
  notification_id?: string | null; acknowledged_at?: string | null; acknowledged_by?: string | null
  acknowledgement_deadline?: string | null; escalation_status?: string
  history?: Array<{event_type: string; occurred_at: string; actor?: string | null; detail: Record<string, any>}>
}
type TimelineEntry = {
  entry_id: string; event_type: string; occurred_at: string; recorded_at: string; recorded_by: string
  payload: Record<string, any>
}
type Investigation = {
  job_id: string; reason: string; cutoff: string; status: string; created_at: string
  completed_at?: string | null; revision?: number | null; report?: any
}
type Detail = Incident & {
  statistics: {alert_windows: number; tickets: number; expected: number; peak_window_tickets?: number}
  investigations: Investigation[]; actions: TimelineEntry[]; lifecycle_history: TimelineEntry[]
  monitoring: {ticket_windows: Array<{window_index: number; window_start: string; window_end: string; tickets: number; usual?: number}>
    service_health: Array<{observed_at: string; service?: string; level?: string; message?: string; errors?: number; requests?: number}>}
  operator_name: string
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

function time(value?: string | null) {
  if (!value) return 'Time unavailable'
  return `${new Date(value).toLocaleString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })} UTC`
}

function appTime(value?: string | null) {
  if (!value) return 'Time unavailable'
  return new Date(value).toLocaleString(undefined, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })
}

function urgencyLabel(level?: Notification['level']) {
  if (!level) return 'Assessment pending'
  return level === 'immediate' ? 'Immediate' : level === 'review_soon' ? 'Review soon' : 'Monitor'
}

function investigationLabel(status?: string | null) {
  if (!status) return 'Investigation not queued'
  if (status === 'queued') return 'Investigation queued'
  if (status === 'running') return 'Investigation running'
  if (status === 'completed') return 'Investigation complete'
  if (status === 'failed' || status === 'exhausted') return 'Investigation failed'
  return `Investigation ${status}`
}

function compareIncidentPriority(a: Incident, b: Incident, attention: Map<string, Notification>) {
  const rank = (incident: Incident) => {
    const item = attention.get(incident.incident_id)
    if (item?.level === 'immediate' && !item.acknowledged_at) return 0
    if (item?.level === 'immediate') return 1
    if (item?.level === 'review_soon') return 2
    if (item?.level === 'monitor') return 3
    return 4
  }
  return rank(a) - rank(b) || b.opened_at.localeCompare(a.opened_at)
}

function orderIncidents(incidents: Incident[], notifications: Notification[]) {
  const attention = new Map(notifications.map((item) => [item.incident_id, item]))
  return [...incidents].sort((a, b) => compareIncidentPriority(a, b, attention))
}

export default function IncidentInbox({ onOpenMonitor }: { onOpenMonitor: () => void }) {
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [runs, setRuns] = useState<ReplayRun[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [filter, setFilter] = useState<'open' | 'resolved' | 'all'>('open')
  const [scope, setScope] = useState<'current' | 'history'>('current')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [ackBusy, setAckBusy] = useState(false)
  const actionKey = useRef(crypto.randomUUID())
  const closureKey = useRef(crypto.randomUUID())
  const reopenKey = useRef(crypto.randomUUID())
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('signalroom-theme') === 'light' ? 'light' : 'dark' } catch { return 'dark' }
  })
  const [actionTime, setActionTime] = useState(() => {
    const local = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    return local.toISOString().slice(0, 16)
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('signalroom-theme', theme) } catch { /* use this session's theme */ }
  }, [theme])

  const refresh = useCallback(async () => {
    try {
      const runList = await request<ReplayRun[]>('/api/runs?limit=100')
      const latestRun = runList[0]
      const streamQuery = scope === 'current'
        ? `?stream_id=${encodeURIComponent(latestRun?.run_id ?? '__no_current_replay__')}` : ''
      const [list, attention] = await Promise.all([
        request<Incident[]>(`/api/incidents${streamQuery}`), request<{items: Notification[]}>(`/api/notifications${streamQuery}`),
      ])
      setRuns(runList)
      setIncidents(list)
      setNotifications(attention.items)
      const ordered = orderIncidents(list, attention.items)
      const filtered = ordered.filter((item) => filter === 'all'
        || item.operational_status === (filter === 'resolved' ? 'resolved' : 'open'))
      setSelectedId((current) => current && filtered.some((item) => item.incident_id === current)
        ? current : filtered[0]?.incident_id ?? '')
      setError('')
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not load the incident inbox.') }
  }, [filter, scope])

  const loadDetail = useCallback(async (id: string) => {
    if (!id) { setDetail(null); return }
    try { setDetail(await request<Detail>(`/api/incidents/${encodeURIComponent(id)}`)) }
    catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not load incident details.') }
  }, [])

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 3000); return () => window.clearInterval(timer) }, [refresh])
  useEffect(() => { void loadDetail(selectedId) }, [loadDetail, selectedId])
  useEffect(() => {
    if (!selectedId) return
    const timer = window.setInterval(() => void loadDetail(selectedId), 3000)
    return () => window.clearInterval(timer)
  }, [loadDetail, selectedId])

  const attentionByIncident = useMemo(() => new Map(notifications.map((item) => [item.incident_id, item])), [notifications])
  const latestRun = runs[0]
  const visible = useMemo(() => incidents.filter((incident) => filter === 'all'
    || (filter === 'open' ? incident.operational_status === 'open' : incident.operational_status === 'resolved'))
    .sort((a, b) => compareIncidentPriority(a, b, attentionByIncident)), [attentionByIncident, filter, incidents])
  const immediateNeedsAck = notifications.filter((item) => item.level === 'immediate'
    && item.operational_status === 'open' && !item.acknowledged_at).length
  const openCount = incidents.filter((item) => item.operational_status === 'open').length
  const investigatingCount = incidents.filter((item) => item.operational_status === 'open'
    && ['queued', 'running'].includes(item.latest_investigation?.status ?? '')).length
  const selectedAttention = detail ? attentionByIncident.get(detail.incident_id) : undefined

  async function submit(path: string, body: Record<string, unknown>): Promise<boolean> {
    if (!detail || saving) return false
    const submittedIncidentId = detail.incident_id
    const closesIncident = path.endsWith('/close')
    setSaving(true); setError('')
    try {
      await request(path, { method: 'POST', body: JSON.stringify(body) })
      await refresh()
      if (closesIncident && filter === 'open') setDetail(null)
      else await loadDetail(submittedIncidentId)
      return true
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'The incident update could not be saved.') }
    finally { setSaving(false) }
    return false
  }

  async function acknowledge() {
    if (!selectedAttention?.notification_id || ackBusy) return
    setAckBusy(true)
    try {
      await request(`/api/notifications/${encodeURIComponent(selectedAttention.notification_id)}/acknowledge`, { method: 'POST' })
      await refresh()
      await loadDetail(selectedAttention.incident_id)
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Could not acknowledge this notification.') }
    finally { setAckBusy(false) }
  }

  const updateForm = (form: HTMLFormElement) => Object.fromEntries(new FormData(form).entries())

  const recovery = useMemo(() => {
    if (!detail) return 'Unknown'
    if (detail.status === 'active') return 'Ongoing'
    const windows = detail.monitoring.ticket_windows
    if (!windows.length) return 'Unknown'
    const latest = windows.at(-1)!
    const baseline = latest.usual
    if (baseline == null) return 'Unknown'
    const peak = Number(detail.statistics.peak_window_tickets ?? 0)
    const current = Number(latest.tickets ?? 0)
    if (current > baseline) return current < peak ? 'Improving' : 'Ongoing'
    const healthKnown = detail.monitoring.service_health.length > 0
    const healthNormal = healthKnown && detail.monitoring.service_health[0].level === 'info'
      && /normal range|within normal/i.test(detail.monitoring.service_health[0].message ?? '')
    return healthNormal ? 'Recovery signs' : 'Improving'
  }, [detail])

  const timeline = useMemo(() => {
    const empty = { replay: [] as Array<{key: string; at: string; title: string; note: string}>, app: [] as Array<{key: string; at: string; title: string; note: string}> }
    if (!detail) return empty
    const replay = [{ key: 'opened', at: detail.opened_at, title: 'Alert episode opened', note: `${detail.category} · ${detail.statistics.alert_windows} alert windows` }]
    const app: Array<{key: string; at: string; title: string; note: string}> = []
    for (const job of detail.investigations) {
      app.push({ key: `${job.job_id}-queued`, at: job.created_at, title: job.reason === 'initial alert' ? 'Investigation queued' : 'Investigation update queued', note: `Evidence cutoff: replay time ${time(job.cutoff)}` })
      if (job.completed_at) app.push({ key: `${job.job_id}-done`, at: job.completed_at, title: `Investigation ${job.revision ? `revision ${job.revision}` : ''} completed`.trim(), note: `Evidence available through replay time ${time(job.cutoff)}` })
    }
    for (const entry of detail.actions) app.push({ key: entry.entry_id, at: entry.recorded_at, title: 'Operator-reported action', note: `${entry.payload.action_description} · reported action time ${time(entry.occurred_at)} · recorded by ${entry.recorded_by}` })
    for (const entry of detail.lifecycle_history) app.push({ key: entry.entry_id, at: entry.recorded_at,
      title: entry.event_type === 'incident_closed' ? 'Incident marked resolved' : 'Incident reopened',
      note: `${entry.payload.reason}${entry.recorded_by ? ` · ${entry.recorded_by}` : ''}` })
    for (const event of selectedAttention?.history ?? []) app.push({ key: `${event.event_type}-${event.occurred_at}`, at: event.occurred_at,
      title: event.event_type.replaceAll('_', ' '), note: event.actor ?? event.detail?.rationale ?? '' })
    if (detail.status === 'ended') replay.push({ key: 'episode-ended', at: detail.latest_window_end, title: 'Alert episode ended', note: 'The first quiet window ended statistical alerting; the incident stayed open.' })
    return { replay: replay.sort((a, b) => a.at.localeCompare(b.at)), app: app.sort((a, b) => a.at.localeCompare(b.at)) }
  }, [detail, selectedAttention])

  return <div className="app-shell inbox-shell">
    <header className="topbar">
      <div className="brand-lockup"><div className="brand-mark"><span/><span/><span/></div><div><div className="brand-name">signal<span>room</span></div><div className="brand-caption">OPERATIONS INTELLIGENCE</div></div></div>
      <nav className="workspace-nav" aria-label="Workspace views"><button className="workspace-nav-active">Incident inbox</button><button onClick={onOpenMonitor}>Live monitor</button></nav>
      <div className="topbar-right"><span className="local-identity">LOCAL OPERATOR</span><button className="theme-toggle" type="button" onClick={() => setTheme((value) => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☼ Light mode' : '☾ Dark mode'}</button></div>
    </header>
    <div className="inbox-main">
      <div className="page-heading inbox-heading"><div><div className="eyebrow"><span className="eyebrow-line"/> OPERATIONS / INCIDENTS</div><h1>Incident inbox</h1><p className="page-subtitle">Review emerging customer-impacting problems and coordinate the response.</p></div><button className="secondary-button" onClick={onOpenMonitor}>Open live monitor</button></div>
      {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError('')}>Dismiss</button></div>}
      <div className="inbox-replay-context">
        <label>Incident scope
          <select value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}>
            <option value="current">Current replay</option><option value="history">Replay history</option>
          </select>
        </label>
        <p>{scope === 'history' ? 'Showing incidents from all saved synthetic replay runs.' : latestRun
          ? `Current replay · ${latestRun.scenario.replaceAll('_', ' ')} · seed ${latestRun.seed} · ${latestRun.status}`
          : 'No replay has been started yet.'}</p>
      </div>
      <section className="inbox-stat-grid" aria-label="Incident summary">
        <article className="panel inbox-stat"><span>OPEN INCIDENTS</span><strong>{openCount}</strong><small>Operationally unresolved</small></article>
        <article className="panel inbox-stat"><span>NEEDS ACKNOWLEDGEMENT</span><strong>{immediateNeedsAck}</strong><small>Immediate notifications without acknowledgement</small></article>
        <article className="panel inbox-stat"><span>INVESTIGATING</span><strong>{investigatingCount}</strong><small>Queued or running investigations</small></article>
      </section>

      <div className="inbox-layout">
        <section className="inbox-list panel" aria-label="Incident list">
          <div className="inbox-list-heading"><div><div className="section-kicker">{scope === 'history' ? 'REPLAY HISTORY' : 'CURRENT REPLAY'}</div><h2>{filter === 'resolved' ? 'Resolved incidents' : filter === 'all' ? 'All incidents' : 'Open incidents'}</h2></div><select aria-label="Filter incident status" value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}><option value="open">Open</option><option value="resolved">Resolved</option><option value="all">All statuses</option></select></div>
          {visible.length === 0 && <p className="inbox-empty">{incidents.length ? 'No incidents match this filter.' : 'No incidents yet. Start a seeded replay in Live monitor to watch the system detect and investigate an event.'}</p>}
          <div className="inbox-cards">{visible.map((incident) => {
            const attention = attentionByIncident.get(incident.incident_id)
            const run = runs.find((item) => item.run_id === incident.stream_id)
            const priority = attention?.level
            return <button key={incident.incident_id} className={`inbox-incident ${selectedId === incident.incident_id ? 'selected' : ''} urgency-${priority ?? 'pending'}`} onClick={() => setSelectedId(incident.incident_id)}>
              <span className="inbox-incident-top"><strong>{incident.category}</strong><span className={`urgency-pill ${priority ?? 'pending'}`}>{urgencyLabel(priority)}</span></span>
              <span className="inbox-incident-status">{investigationLabel(incident.latest_investigation?.status)}</span>
              <span className="inbox-incident-title">{incident.statistics.tickets} tickets in {incident.statistics.alert_windows} alert windows · {incident.statistics.expected.toFixed(1)} expected</span>
              <span className="inbox-incident-meta">{attention?.rationale ?? `No urgency assessment yet. ${investigationLabel(incident.latest_investigation?.status)}.`}</span>
              <span className="inbox-incident-meta">Episode {incident.status === 'active' ? 'alerting' : 'ended'} · Incident {incident.operational_status === 'open' ? 'open' : 'resolved'}</span>
              <span className="inbox-incident-meta">Episode began · replay time {time(incident.opened_at)}</span>
              <span className="inbox-incident-meta">{incident.status === 'active' ? 'Latest alert window ended' : 'Episode ended'} · replay time {time(incident.latest_window_end)}</span>
              {scope === 'history' && <span className="inbox-incident-meta">Replay seed {run?.seed ?? 'unknown'} · started in app {run ? appTime(run.created_at) : incident.stream_id.slice(0, 8)}</span>}
            </button>
          })}</div>
        </section>

        <section className="incident-workspace panel" aria-label="Selected incident workspace">
          {(!detail || detail.incident_id !== selectedId) && <div className="workspace-empty"><span>◷</span><h2>Select an incident</h2><p>Findings, response history, monitoring, and human actions will appear here.</p></div>}
          {detail && detail.incident_id === selectedId && <>
            <div className="workspace-title-row"><div><div className="section-kicker">{detail.category.toUpperCase()} · INCIDENT WORKSPACE</div><h2>{detail.category} incident</h2><p>Episode began · replay time {time(detail.opened_at)} · {detail.status === 'active' ? 'Alert episode active' : 'Alert episode ended'} · Incident {detail.operational_status === 'open' ? 'open' : 'resolved'}</p></div><div className="incident-statuses"><span className={`urgency-pill ${selectedAttention?.level ?? 'pending'}`}>{urgencyLabel(selectedAttention?.level)}</span><span className="investigation-state-pill">{investigationLabel(detail.investigations.at(-1)?.status)}</span></div></div>
            <div className="incident-metric-row"><div><span>ALERT WINDOWS</span><strong>{detail.statistics.alert_windows}</strong></div><div><span>TICKETS</span><strong>{detail.statistics.tickets}</strong></div><div><span>EXPECTED</span><strong>{detail.statistics.expected.toFixed(1)}</strong></div><div><span>PEAK WINDOW</span><strong>{detail.statistics.peak_window_tickets ?? '—'}</strong></div></div>
            {selectedAttention ? <section className={`workspace-attention attention-${selectedAttention.level}`}><div className="workspace-attention-heading"><strong>{urgencyLabel(selectedAttention.level)} · attention assessment</strong>{selectedAttention.level === 'immediate' && !selectedAttention.acknowledged_at && selectedAttention.notification_id && <button className="secondary-button" disabled={ackBusy} onClick={() => void acknowledge()}>{ackBusy ? 'Saving…' : 'Acknowledge'}</button>}</div><p>{selectedAttention.rationale}</p>{selectedAttention.level === 'immediate' && <p className="attention-ack-state">{selectedAttention.acknowledged_at ? `Acknowledged by ${selectedAttention.acknowledged_by} · app time ${appTime(selectedAttention.acknowledged_at)}` : `Needs acknowledgement by app time ${appTime(selectedAttention.acknowledgement_deadline)}`}</p>}</section>
              : <section className="workspace-attention attention-pending"><div className="workspace-attention-heading"><strong>Priority assessment pending</strong></div><p>The alert is visible. No urgency assessment has been recorded yet.</p></section>}

            <section className="workspace-section"><div className="workspace-section-heading"><div><div className="section-kicker">INVESTIGATION</div><h3>Findings and evidence scope</h3></div></div>
              {detail.investigations.length === 0 && <p className="inbox-empty">No investigation is recorded yet.</p>}
              {detail.investigations.map((job) => {
                const report = job.report
                const hypothesis = report?.hypotheses?.[0]
                const evidenceById = new Map((report?.evidence ?? []).map((item: any) => [item.evidence_id, item]))
                const recommendation = report?.recommendations?.find((item: any) => item.category === detail.category)
                  ?? report?.recommendations?.[0]
                return <article className="investigation-revision" key={job.job_id}>
                  <div className="revision-heading"><strong>{job.revision ? `Revision ${job.revision}` : job.reason}</strong><span>{job.status}</span></div>
                  <small>Evidence cutoff · replay time {time(job.cutoff)} · {job.completed_at ? `completed · app time ${appTime(job.completed_at)}` : `queued · app time ${appTime(job.created_at)}`}</small>
                  {report && <>
                    <h4>{hypothesis?.title ?? (report.status === 'abstained' ? 'Evidence remains inconclusive' : 'Investigation findings')}</h4>
                    <p>{report.summary}</p>
                    {recommendation && <div className="workspace-recommendation"><span>ADVISORY NEXT STEP</span><strong>{recommendation.title}</strong><p>{recommendation.next_step}</p><small>Verify: {recommendation.verification}</small></div>}
                    <details><summary>Evidence and uncertainty</summary>
                      {hypothesis?.explanation && <p>{hypothesis.explanation}</p>}
                      {hypothesis?.supporting_evidence?.length > 0 && <div className="workspace-citations"><strong>Evidence supporting this finding</strong>{hypothesis.supporting_evidence.map((id: string) => {
                        const evidence = evidenceById.get(id) as any
                        return <p key={id}>{evidence ? `${evidence.source} · replay time ${time(evidence.observed_at)} · ${evidence.summary}` : 'Referenced evidence record unavailable.'}</p>
                      })}</div>}
                      {hypothesis?.contradicting_evidence?.length > 0 && <div className="workspace-citations"><strong>Evidence that does not fit</strong>{hypothesis.contradicting_evidence.map((id: string) => {
                        const evidence = evidenceById.get(id) as any
                        return <p key={id}>{evidence ? `${evidence.source} · replay time ${time(evidence.observed_at)} · ${evidence.summary}` : 'Referenced evidence record unavailable.'}</p>
                      })}</div>}
                      <strong>What remains uncertain</strong><ul>{(report.unresolved_questions ?? []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>
                    </details>
                  </>}
                </article>
              })}
            </section>

            <section className="workspace-section"><div className="workspace-section-heading"><div><div className="section-kicker">MONITORING</div><h3>After the alert</h3></div><span className={`recovery-label ${recovery.toLowerCase().replaceAll(' ', '-')}`}>{recovery}</span></div>
              <p className="workspace-note">These observations show what changed after the alert. They do not prove a cause or confirm every customer is recovered. Replay event times and app record times use separate clocks.</p>
              {detail.monitoring.ticket_windows.length > 0 ? <div className="trend-table"><div className="trend-row trend-head"><span>Replay window · synthetic UTC</span><span>Tickets</span><span>Usual</span></div>{detail.monitoring.ticket_windows.map((window) => <div className="trend-row" key={window.window_index}><span>{time(window.window_start)} – {time(window.window_end).replace(' UTC', '')}</span><strong>{window.tickets}</strong><span>{window.usual == null ? '—' : Number(window.usual).toFixed(1)}</span></div>)}</div> : <p className="inbox-empty">No completed post-alert count windows are available yet.</p>}
              <h4>Service health evidence</h4>{detail.monitoring.service_health.length ? <ul className="health-evidence-list">{detail.monitoring.service_health.slice(0, 4).map((record, index) => <li key={`${record.observed_at}-${index}`}><span>Replay time {time(record.observed_at)} · {record.service ?? 'Service'}</span><strong>{record.message ?? 'Service log observed'}</strong>{record.errors != null && <small>{record.errors} errors across {record.requests ?? '—'} requests</small>}</li>)}</ul> : <p className="inbox-empty">No matching service-health records are available after this incident began.</p>}
            </section>

            <section className="workspace-section"><div className="section-kicker">INCIDENT TIMELINE</div><h3>What happened</h3>
              <h4>Replay events · synthetic event time</h4><ol className="incident-timeline">{timeline.replay.map((entry) => <li key={entry.key}><span className="timeline-marker"/><div><strong>{entry.title}</strong><time>{time(entry.at)}</time>{entry.note && <p>{entry.note}</p>}</div></li>)}</ol>
              <h4>Work in this app · app time</h4>{timeline.app.length ? <ol className="incident-timeline">{timeline.app.map((entry) => <li key={entry.key}><span className="timeline-marker"/><div><strong>{entry.title}</strong><time>{appTime(entry.at)}</time>{entry.note && <p>{entry.note}</p>}</div></li>)}</ol> : <p className="inbox-empty">No operator or application activity has been recorded yet.</p>}
            </section>

            {detail.operational_status === 'open' ? <section className="workspace-action-grid">
              <form className="workspace-form panel" onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const values = updateForm(form); const occurred = new Date(String(values.occurred_at)); void submit(`/api/incidents/${detail.incident_id}/actions`, { action_description: values.action_description, occurred_at: occurred.toISOString(), performed_by: values.performed_by, reference: values.reference, idempotency_key: actionKey.current }).then((saved) => { if (saved) { form.reset(); actionKey.current = crypto.randomUUID() } }) }}>
                <div className="section-kicker">HUMAN-REPORTED · NO REMEDIATION EXECUTED HERE</div><h3>Record an action</h3><p>Log what someone did outside Signalroom. The entry is reported, not independently verified.</p>
                <label>What happened<textarea name="action_description" required maxLength={2000} placeholder="Payments engineering reported rolling back the gateway configuration"/></label>
                <label>When did it happen?<input name="occurred_at" type="datetime-local" required value={actionTime} onChange={(event) => setActionTime(event.target.value)}/></label>
                <label>Who performed it? <span className="optional">Optional</span><input name="performed_by" maxLength={200} placeholder="Payments engineering"/></label>
                <label>Reference <span className="optional">Optional</span><input name="reference" maxLength={500} placeholder="Change or incident ticket"/></label>
                <small>Recorded by {detail.operator_name} · local demo identity, not verified</small><button className="primary-button" disabled={saving}>Save action to timeline</button>
              </form>
              <form className="workspace-form close-form panel" onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const values = updateForm(form); void submit(`/api/incidents/${detail.incident_id}/close`, { reason: values.reason, supporting_context: values.supporting_context, idempotency_key: closureKey.current }).then((saved) => { if (saved) { form.reset(); closureKey.current = crypto.randomUUID() } }) }}>
                <div className="section-kicker">HUMAN DECISION</div><h3>Resolve this incident</h3><p>A quiet window, acknowledgement, or improving trend does not resolve an incident by itself.</p>
                <label>Resolution reason<textarea name="reason" required maxLength={1000} placeholder="What confirms the coordinated issue is resolved?"/></label>
                <label>Supporting context <span className="optional">Optional</span><textarea name="supporting_context" maxLength={2000} placeholder="Evidence or update from the responsible team"/></label>
                <small>Closed by {detail.operator_name} · local demo identity, not verified</small><button className="secondary-button" disabled={saving}>Mark incident resolved</button>
              </form>
            </section> : <section className="workspace-form reopen-form panel"><div className="section-kicker">INCIDENT RESOLVED</div><h3>Reopen if the problem is still active</h3><form onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const values = updateForm(form); void submit(`/api/incidents/${detail.incident_id}/reopen`, { reason: values.reason, idempotency_key: reopenKey.current }).then((saved) => { if (saved) { form.reset(); reopenKey.current = crypto.randomUUID() } }) }}><label>Why reopen this incident?<textarea name="reason" required maxLength={1000} placeholder="New evidence shows customers are still affected"/></label><small>Reopening preserves the previous closure in the timeline.</small><button className="secondary-button" disabled={saving}>Reopen incident</button></form></section>}
          </>}
        </section>
      </div>
      <p className="workspace-disclaimer">Synthetic replay · operator identity is configured locally · no remediation is performed by this application.</p>
    </div>
  </div>
}
