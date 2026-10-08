/**
 * Microphone capture in an AudioWorklet: raw audio is converted to 16 kHz
 * PCM16 on the audio thread and posted to the main thread in 40 ms chunks,
 * together with the chunk's loudness (RMS) for the audio-reactive bubble.
 *
 * Why an AudioWorklet and not MediaRecorder: MediaRecorder produces
 * compressed WebM blobs - fine for "record, then upload", useless for
 * live streaming to a VAD. A worklet gets every 128-sample block as it
 * arrives, so audio reaches the server with ~40 ms delay.
 *
 * The worklet code is built from a string (plus the Downsampler class
 * source), so there is exactly one implementation of the resampler and it
 * is unit-tested (see __tests__/captureWorklet.test.ts).
 */
import { Downsampler } from "./downsampler";

export const TARGET_SAMPLE_RATE = 16000;
export const PROCESSOR_NAME = "cypher-pcm-capture";
const CHUNK_MS = 40;

const PROCESSOR_SOURCE = `
class CypherPcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.down = new Downsampler(sampleRate, opts.targetRate);
    this.chunk = opts.chunkSamples;
    this.buf = new Int16Array(this.chunk);
    this.fill = 0;
    this.sumSq = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    const out = this.down.process(channel);
    for (let i = 0; i < out.length; i++) {
      const s = out[i];
      this.buf[this.fill++] = s;
      this.sumSq += (s / 32768) * (s / 32768);
      if (this.fill === this.chunk) {
        const rms = Math.sqrt(this.sumSq / this.chunk);
        this.port.postMessage({ pcm: this.buf.buffer, rms: rms }, [this.buf.buffer]);
        this.buf = new Int16Array(this.chunk);
        this.fill = 0;
        this.sumSq = 0;
      }
    }
    return true;
  }
}
registerProcessor(${JSON.stringify(PROCESSOR_NAME)}, CypherPcmCapture);
`;

export function buildWorkletSource(): string {
  return `const Downsampler = (${Downsampler.toString()});\n${PROCESSOR_SOURCE}`;
}

export interface CaptureChunk {
  pcm: ArrayBuffer; // Int16 LE mono, 16 kHz
  rms: number;
}

export interface MicCapture {
  stream: MediaStream;
  context: AudioContext;
  stop: () => Promise<void>;
}

/** Ask for the microphone (with the browser's echo cancellation - Cypher's
 * own voice must not trigger the wake word) and start streaming chunks. */
export async function startMicCapture(onChunk: (chunk: CaptureChunk) => void): Promise<MicCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const context = new AudioContext({ latencyHint: "interactive" });
  try {
    const url = URL.createObjectURL(new Blob([buildWorkletSource()], { type: "application/javascript" }));
    try {
      await context.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, PROCESSOR_NAME, {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      processorOptions: { targetRate: TARGET_SAMPLE_RATE, chunkSamples: (TARGET_SAMPLE_RATE * CHUNK_MS) / 1000 },
    });
    node.port.onmessage = (event: MessageEvent<CaptureChunk>) => onChunk(event.data);
    source.connect(node);
    if (context.state === "suspended") await context.resume();
    return {
      stream,
      context,
      stop: async () => {
        node.port.onmessage = null;
        source.disconnect();
        node.disconnect();
        stream.getTracks().forEach((track) => track.stop());
        await context.close();
      },
    };
  } catch (err) {
    stream.getTracks().forEach((track) => track.stop());
    await context.close();
    throw err;
  }
}
