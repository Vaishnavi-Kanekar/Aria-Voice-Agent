import { useCallback, useEffect, useRef, useState } from 'react'
import { PcmPlayer } from './audio/player'

// status: idle | connecting | active | ending | ended
export function useCall() {
  const [status, setStatusState] = useState('idle')
  const [serverState, setServerState] = useState('listening')
  const [playing, setPlaying] = useState(false)
  const [messages, setMessages] = useState([])
  const [partial, setPartial] = useState('')
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)

  const statusRef = useRef('idle')
  const r = useRef({ idCounter: 0 })

  const setStatus = (s) => {
    statusRef.current = s
    setStatusState(s)
  }

  const stopAudio = useCallback(() => {
    const c = r.current
    c.ready = false
    c.player?.clear()
    c.stream?.getTracks().forEach((t) => t.stop())
    try {
      c.node?.disconnect()
    } catch {
      /* ignore */
    }
    c.ctx?.close().catch(() => {})
    c.stream = c.node = c.ctx = c.player = null
  }, [])

  const finish = useCallback(() => {
    clearTimeout(r.current.summaryTimer)
    stopAudio()
    try {
      r.current.ws?.close()
    } catch {
      /* ignore */
    }
    r.current.ws = null
    setPlaying(false)
    setPartial('')
    setStatus('ended')
  }, [stopAudio])

  const handleMessage = useCallback(
    (ev) => {
      const c = r.current
      if (ev.data instanceof ArrayBuffer) {
        c.player?.enqueue(ev.data)
        return
      }
      const msg = JSON.parse(ev.data)
      switch (msg.type) {
        case 'ready':
          c.player = new PcmPlayer(c.ctx, msg.tts_sample_rate, setPlaying)
          c.ready = true
          setStatus('active')
          break
        case 'state':
          setServerState(msg.state)
          break
        case 'partial':
          setPartial(msg.text)
          break
        case 'user_text':
          setPartial('')
          setMessages((m) => [...m, { id: ++c.idCounter, role: 'user', text: msg.text }])
          break
        case 'agent_text':
          setMessages((m) => {
            const last = m[m.length - 1]
            if (last && last.role === 'agent' && last.turnId === msg.turn_id) {
              return [...m.slice(0, -1), { ...last, text: `${last.text} ${msg.text}` }]
            }
            return [...m, { id: ++c.idCounter, role: 'agent', turnId: msg.turn_id, text: msg.text }]
          })
          break
        case 'metrics':
          setMessages((m) =>
            m.map((x) => (x.role === 'agent' && x.turnId === msg.turn_id ? { ...x, latencyMs: msg.first_audio_ms } : x)),
          )
          break
        case 'clear_audio':
          c.player?.clear()
          setMessages((m) => {
            const i = m.map((x) => x.role).lastIndexOf('agent')
            return i < 0 ? m : m.map((x, j) => (j === i ? { ...x, interrupted: true } : x))
          })
          break
        case 'error':
          setError(msg.message)
          if (msg.fatal) finish()
          break
        case 'summary':
          setSummary(msg)
          finish()
          break
        default:
          break
      }
    },
    [finish],
  )

  const start = useCallback(async () => {
    setError(null)
    setSummary(null)
    setMessages([])
    setPartial('')
    setServerState('listening')
    setStatus('connecting')
    const c = r.current
    try {
      c.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      c.ctx = new AudioContext({ latencyHint: 'interactive' })
      await c.ctx.resume()
      await c.ctx.audioWorklet.addModule('/mic-worklet.js')
      c.node = new AudioWorkletNode(c.ctx, 'mic-processor')
      c.ctx.createMediaStreamSource(c.stream).connect(c.node)

      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws/call`)
      ws.binaryType = 'arraybuffer'
      c.ws = ws
      c.node.port.onmessage = (e) => {
        if (c.ready && ws.readyState === WebSocket.OPEN) ws.send(e.data)
      }
      ws.onopen = () => ws.send(JSON.stringify({ type: 'start' }))
      ws.onmessage = handleMessage
      ws.onerror = () => setError('Could not reach the server. Is the backend running?')
      ws.onclose = () => {
        if (statusRef.current !== 'ended' && statusRef.current !== 'idle') finish()
      }
    } catch (e) {
      stopAudio()
      setStatus('idle')
      setError(
        e.name === 'NotAllowedError'
          ? 'Microphone permission was denied. Allow microphone access and try again.'
          : `Could not start the call: ${e.message}`,
      )
    }
  }, [finish, handleMessage, stopAudio])

  const end = useCallback(() => {
    const ws = r.current.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      finish()
      return
    }
    setStatus('ending')
    stopAudio() // stop the mic and any speech immediately
    setPlaying(false)
    ws.send(JSON.stringify({ type: 'end' }))
    // The server builds the summary with an LLM call; do not wait forever.
    r.current.summaryTimer = setTimeout(() => {
      setError('The call summary took too long to generate.')
      finish()
    }, 35000)
  }, [finish, stopAudio])

  useEffect(
    () => () => {
      clearTimeout(r.current.summaryTimer)
      stopAudio()
      r.current.ws?.close()
    },
    [stopAudio],
  )

  const displayState =
    status === 'idle' || status === 'ended'
      ? 'idle'
      : status === 'connecting'
        ? 'connecting'
        : status === 'ending'
          ? 'ending'
          : serverState === 'thinking'
            ? 'thinking'
            : playing || serverState === 'speaking'
              ? 'speaking'
              : 'listening'

  return { status, displayState, messages, partial, summary, error, start, end }
}
