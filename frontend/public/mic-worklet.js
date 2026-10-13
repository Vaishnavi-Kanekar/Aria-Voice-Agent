// AudioWorklet: converts the microphone stream to 16 kHz mono PCM16 and posts ~32 ms frames.
// Runs off the main thread so UI work can never cause audio glitches.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / 16000 // input samples per output sample
    this.pos = 0 // fractional read position relative to the current block (>= -1)
    this.last = 0 // last sample of the previous block, for interpolation across blocks
    this.frame = new Int16Array(512)
    this.n = 0
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0]
    if (!input) return true
    const len = input.length
    let pos = this.pos
    while (pos < len - 1) {
      const i = Math.floor(pos)
      const frac = pos - i
      const a = i < 0 ? this.last : input[i]
      const b = input[i + 1]
      const s = Math.max(-1, Math.min(1, a + (b - a) * frac)) // linear interpolation
      this.frame[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff
      if (this.n === this.frame.length) {
        const copy = this.frame.slice()
        this.port.postMessage(copy.buffer, [copy.buffer])
        this.n = 0
      }
      pos += this.ratio
    }
    this.pos = pos - len
    this.last = input[len - 1]
    return true
  }
}

registerProcessor('mic-processor', MicProcessor)
