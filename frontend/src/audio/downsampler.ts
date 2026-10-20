/**
 * Microphone sample rate (usually 48 kHz) -> 16 kHz PCM16, the format the
 * backend's VAD and Whisper expect.
 *
 * Each output sample is the average of the input samples it covers (a box
 * filter). That's a crude low-pass, but it removes most of what would
 * otherwise alias into the speech band, and it's cheap enough to run in the
 * audio thread. Works for non-integer ratios (44.1 kHz) by carrying the
 * fractional read position across chunks.
 *
 * IMPORTANT: this class is also stringified into the AudioWorklet
 * (see captureWorklet.ts), so it must stay self-contained: no imports and
 * no real class fields (the build turns them into helper calls that don't
 * exist inside the worklet) - a test checks the minified build.
 */
export class Downsampler {
  // `declare`: type-only fields. Plain field declarations would be compiled
  // to helper calls (__publicField) that don't exist inside the worklet.
  declare ratio: number;
  declare pending: Float32Array;
  declare position: number;

  constructor(inputRate: number, outputRate: number) {
    this.ratio = inputRate / outputRate;
    this.pending = new Float32Array(0);
    this.position = 0;
  }

  process(input: Float32Array): Int16Array {
    const buffer = new Float32Array(this.pending.length + input.length);
    buffer.set(this.pending, 0);
    buffer.set(input, this.pending.length);
    const count = Math.max(0, Math.floor((buffer.length - this.position) / this.ratio));
    const out = new Int16Array(count);
    let start = this.position;
    for (let i = 0; i < count; i++) {
      const end = start + this.ratio;
      const last = Math.min(Math.ceil(end), buffer.length);
      let sum = 0;
      let n = 0;
      for (let j = Math.floor(start); j < last; j++) {
        sum += buffer[j];
        n++;
      }
      const value = n > 0 ? Math.max(-1, Math.min(1, sum / n)) : 0;
      out[i] = value < 0 ? Math.round(value * 0x8000) : Math.round(value * 0x7fff);
      start = end;
    }
    const consumed = Math.floor(start);
    this.pending = buffer.slice(consumed);
    this.position = start - consumed;
    return out;
  }
}
