import { useEffect, useRef } from 'react'

export default function Transcript({ messages, partial }) {
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, partial])

  if (messages.length === 0 && !partial) return <p className="muted">Waiting for the conversation to start...</p>

  return (
    <div className="chat">
      {messages.map((m) => (
        <div key={m.id} className={`bubble ${m.role}`}>
          <div className="who">{m.role === 'user' ? 'You' : 'Aria'}</div>
          <div>
            {m.text}
            {m.interrupted && <span className="tag"> (interrupted)</span>}
          </div>
          {m.latencyMs != null && <div className="latency">{m.latencyMs} ms to first audio</div>}
        </div>
      ))}
      {partial && (
        <div className="bubble user partial">
          <div className="who">You</div>
          <div>{partial}</div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}
