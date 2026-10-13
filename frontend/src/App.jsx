import { useCall } from './useCall'
import StateIndicator from './components/StateIndicator'
import OrdersPanel from './components/OrdersPanel'
import Transcript from './components/Transcript'
import SummaryPanel from './components/SummaryPanel'

export default function App() {
  const { status, displayState, messages, partial, summary, error, start, end } = useCall()
  const inCall = status === 'connecting' || status === 'active' || status === 'ending'

  return (
    <div className="page">
      <header className="header">
        <h1>Aura Skincare</h1>
        <span className="subtitle">Voice support with Aria</span>
      </header>

      <div className="layout">
        <main className="main">
          <section className="card call-card">
            <StateIndicator state={displayState} />
            <div className="controls">
              <button className="btn primary" onClick={start} disabled={inCall}>
                {status === 'connecting' ? 'Connecting...' : status === 'ended' ? 'Start New Call' : 'Start Call'}
              </button>
              <button className="btn danger" onClick={end} disabled={status !== 'active'}>
                End Call
              </button>
            </div>
            <p className="hint">
              Allow microphone access, then just talk. Headphones give the cleanest experience; you can interrupt Aria at any time.
            </p>
            {error && <div className="error">{error}</div>}
          </section>

          {(inCall || (messages.length > 0 && !summary)) && (
            <section className="card">
              <h2>Live conversation</h2>
              <Transcript messages={messages} partial={partial} />
            </section>
          )}

          {status === 'ending' && <p className="muted">Generating call summary...</p>}
          {summary && <SummaryPanel summary={summary} />}
        </main>

        <aside className="side">
          <OrdersPanel />
        </aside>
      </div>
    </div>
  )
}
