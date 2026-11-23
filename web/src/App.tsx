import EventDrivenMonitor from './EventDrivenMonitor'
import IncidentInbox from './IncidentInbox'
import { useState } from 'react'

export default function App() {
  const [view, setView] = useState<'inbox' | 'monitor'>(() =>
    new URLSearchParams(window.location.search).has('run') ? 'monitor' : 'inbox')
  return view === 'inbox'
    ? <IncidentInbox onOpenMonitor={() => setView('monitor')} />
    : <EventDrivenMonitor onOpenInbox={() => setView('inbox')} />
}
