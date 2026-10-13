// Gapless playback of streamed PCM16 chunks, with instant stop for barge-in.
export class PcmPlayer {
  constructor(ctx, sampleRate, onPlayingChange) {
    this.ctx = ctx
    this.sampleRate = sampleRate
    this.onPlayingChange = onPlayingChange
    this.sources = new Set()
    this.nextTime = 0
  }

  enqueue(arrayBuffer) {
    const n = arrayBuffer.byteLength >> 1
    if (!n) return
    const pcm = new Int16Array(arrayBuffer, 0, n)
    const buffer = this.ctx.createBuffer(1, n, this.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < n; i++) channel[i] = pcm[i] / 32768

    const src = this.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(this.ctx.destination)

    // Schedule each chunk right after the previous one. If we fell behind (first chunk or a
    // network hiccup), restart with a small lead so playback does not stutter.
    const now = this.ctx.currentTime
    if (this.nextTime < now + 0.03) this.nextTime = now + 0.1
    src.start(this.nextTime)
    this.nextTime += buffer.duration

    this.sources.add(src)
    src.onended = () => {
      this.sources.delete(src)
      if (this.sources.size === 0) this.onPlayingChange(false)
    }
    this.onPlayingChange(true)
  }

  clear() {
    for (const src of this.sources) {
      src.onended = null
      try {
        src.stop()
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear()
    this.nextTime = 0
    this.onPlayingChange(false)
  }
}
