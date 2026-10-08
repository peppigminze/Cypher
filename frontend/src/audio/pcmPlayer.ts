/**
 * Gapless playback of Cypher's voice: the server sends one PCM16 chunk per
 * sentence while the model is still writing the next one. Each chunk is
 * scheduled to start exactly when the previous one ends (Web Audio clock),
 * so sentences join without clicks or pauses.
 *
 * stop() drops everything at once - that is what barge-in feels like:
 * you talk, Cypher is silent immediately, not after the current sentence.
 */

export function pcm16ToFloat32(pcm: ArrayBuffer): Float32Array<ArrayBuffer> {
  const ints = new Int16Array(pcm, 0, Math.floor(pcm.byteLength / 2));
  const out = new Float32Array(ints.length);
  for (let i = 0; i < ints.length; i++) out[i] = ints[i] / 32768;
  return out;
}

/** Small lead so the first chunk isn't scheduled in the past. */
const START_LEAD_SECONDS = 0.04;

export class PcmPlayer {
  readonly context: AudioContext;
  readonly analyser: AnalyserNode;
  onStarted: ((turnId: number) => void) | null = null;
  onDone: ((turnId: number) => void) | null = null;

  private nextStart = 0;
  private active = new Set<AudioBufferSourceNode>();
  private turnId: number | null = null;
  private turnEnded = false;
  private startedTurns = new Set<number>();
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private levelData: Uint8Array<ArrayBuffer>;

  constructor(context?: AudioContext) {
    this.context = context ?? new AudioContext({ latencyHint: "interactive" });
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.connect(this.context.destination);
    this.levelData = new Uint8Array(this.analyser.fftSize);
  }

  /** Browsers start audio contexts suspended until a user gesture. */
  async resume(): Promise<void> {
    if (this.context.state === "suspended") await this.context.resume();
  }

  enqueue(turnId: number, pcm: ArrayBuffer, sampleRate: number): void {
    if (this.turnId !== turnId) {
      this.turnId = turnId;
      this.turnEnded = false;
    }
    const samples = pcm16ToFloat32(pcm);
    if (samples.length === 0) return;
    const buffer = this.context.createBuffer(1, samples.length, sampleRate);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    const now = this.context.currentTime;
    const startAt = Math.max(now + START_LEAD_SECONDS, this.nextStart);
    this.nextStart = startAt + buffer.duration;
    this.active.add(source);
    source.onended = () => {
      this.active.delete(source);
      this.checkDone();
    };
    source.start(startAt);
    if (!this.startedTurns.has(turnId)) {
      this.startedTurns.add(turnId);
      const timer = setTimeout(
        () => {
          this.timers.delete(timer);
          if (this.turnId === turnId) this.onStarted?.(turnId);
        },
        Math.max(0, (startAt - now) * 1000)
      );
      this.timers.add(timer);
    }
  }

  /** The server sent everything for this turn; report "done" once it played. */
  markEnd(turnId: number): void {
    if (this.turnId !== turnId) {
      // Nothing was played for this turn (e.g. a tool-only answer).
      this.onDone?.(turnId);
      return;
    }
    this.turnEnded = true;
    this.checkDone();
  }

  /** Barge-in: silence now and forget everything queued. */
  stop(): void {
    for (const source of this.active) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    }
    this.active.clear();
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    this.nextStart = 0;
    this.turnId = null;
    this.turnEnded = false;
  }

  get playing(): boolean {
    return this.active.size > 0;
  }

  /** 0..1 loudness of what is playing right now (for the bubble). */
  level(): number {
    if (!this.playing) return 0;
    this.analyser.getByteTimeDomainData(this.levelData);
    let sum = 0;
    for (let i = 0; i < this.levelData.length; i++) {
      const v = (this.levelData[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / this.levelData.length) * 3);
  }

  async close(): Promise<void> {
    this.stop();
    await this.context.close();
  }

  private checkDone(): void {
    if (this.turnEnded && this.active.size === 0 && this.turnId !== null) {
      const turn = this.turnId;
      this.turnEnded = false;
      this.onDone?.(turn);
    }
  }
}
