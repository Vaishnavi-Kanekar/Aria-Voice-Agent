const LABELS = {
  idle: ['Not in a call', 'Click Start Call to begin'],
  connecting: ['Connecting', 'Setting up the microphone and server'],
  listening: ['Listening', 'Aria is listening to you'],
  thinking: ['Thinking', 'Aria is working out a reply'],
  speaking: ['Speaking', 'Aria is speaking (you can interrupt)'],
  ending: ['Wrapping up', 'Preparing your call summary'],
}

export default function StateIndicator({ state }) {
  const [label, hint] = LABELS[state] ?? LABELS.idle
  return (
    <div className={`state state-${state}`} role="status" aria-live="polite">
      <span className="dot" />
      <div>
        <div className="state-label">{label}</div>
        <div className="state-hint">{hint}</div>
      </div>
    </div>
  )
}
