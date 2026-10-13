import { useState } from 'react'

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export default function SummaryPanel({ summary }) {
  const [copied, setCopied] = useState(false)
  const json = JSON.stringify(summary.outcome, null, 2)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <>
      <section className="card">
        <h2>Call outcome</h2>
        <p className="muted small">Call length {fmt(summary.duration_s)}</p>
        <pre className="json">{json}</pre>
        <button className="btn small" onClick={copy}>
          {copied ? 'Copied' : 'Copy JSON'}
        </button>
      </section>

      <section className="card">
        <h2>Call transcript</h2>
        {summary.transcript.length === 0 && <p className="muted">No conversation was recorded.</p>}
        <div className="chat">
          {summary.transcript.map((t, i) => (
            <div key={i} className={`bubble ${t.role}`}>
              <div className="who">
                {t.role === 'user' ? 'Customer' : 'Aria'} <span className="muted">&middot; {fmt(t.t)}</span>
              </div>
              <div>
                {t.text}
                {t.interrupted && <span className="tag"> (interrupted)</span>}
              </div>
              {t.latency_ms != null && <div className="latency">{t.latency_ms} ms to first audio</div>}
            </div>
          ))}
        </div>

        {summary.tool_calls?.length > 0 && (
          <details className="tools">
            <summary>Tool calls ({summary.tool_calls.length})</summary>
            <pre className="json">{JSON.stringify(summary.tool_calls, null, 2)}</pre>
          </details>
        )}
      </section>
    </>
  )
}
