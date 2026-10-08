// @vitest-environment node
// (esbuild, used to check the minified worklet, refuses to run under jsdom)
import { describe, expect, it, vi } from "vitest";
import { build } from "esbuild";
import { Downsampler } from "../downsampler";
import { buildWorkletSource, PROCESSOR_NAME } from "../captureWorklet";
import { PcmPlayer, pcm16ToFloat32 } from "../pcmPlayer";

function sine(rate: number, freq: number, seconds: number): Float32Array {
  const out = new Float32Array(Math.round(rate * seconds));
  for (let i = 0; i < out.length; i++) out[i] = 0.5 * Math.sin((2 * Math.PI * freq * i) / rate);
  return out;
}

describe("Downsampler", () => {
  it("turns 48 kHz into 16 kHz PCM16 with the right length", () => {
    const down = new Downsampler(48000, 16000);
    const out = down.process(sine(48000, 440, 1));
    expect(out.length).toBe(16000);
    const peak = Math.max(...Array.from(out, Math.abs));
    expect(peak).toBeGreaterThan(0.45 * 0x7fff);
    expect(peak).toBeLessThanOrEqual(0x7fff);
  });

  it("handles 44.1 kHz streamed in 128-sample blocks without drift", () => {
    const down = new Downsampler(44100, 16000);
    const input = sine(44100, 300, 2);
    let total = 0;
    for (let i = 0; i < input.length; i += 128) total += down.process(input.subarray(i, i + 128)).length;
    expect(Math.abs(total - 32000)).toBeLessThanOrEqual(1);
  });

  it("is a passthrough at 16 kHz and clips instead of wrapping", () => {
    const down = new Downsampler(16000, 16000);
    const out = down.process(new Float32Array([0, 0.5, -0.5, 2, -2]));
    expect(Array.from(out)).toEqual([0, Math.round(0.5 * 0x7fff), Math.round(-0.5 * 0x8000), 0x7fff, -0x8000]);
  });
});

describe("capture worklet source", () => {
  it("runs inside a simulated AudioWorkletGlobalScope and posts 40 ms PCM16 chunks", () => {
    const registered: Record<string, new (options: unknown) => { process: (i: Float32Array[][]) => boolean; port: { postMessage: (m: unknown) => void } }> = {};
    class FakeProcessor {
      port = { postMessage: vi.fn() };
    }
    // The worklet is plain code with globals - evaluate it like the browser would.
    new Function("AudioWorkletProcessor", "registerProcessor", "sampleRate", buildWorkletSource())(
      FakeProcessor,
      (name: string, ctor: (typeof registered)[string]) => (registered[name] = ctor),
      48000
    );
    const Processor = registered[PROCESSOR_NAME];
    expect(Processor).toBeDefined();
    const processor = new Processor({ processorOptions: { targetRate: 16000, chunkSamples: 640 } });
    const input = sine(48000, 440, 0.1);
    for (let i = 0; i < input.length; i += 128) processor.process([[input.subarray(i, i + 128)]]);
    const posted = (processor.port.postMessage as ReturnType<typeof vi.fn>).mock.calls;
    expect(posted.length).toBe(2); // 100 ms -> two full 40 ms chunks
    const { pcm, rms } = posted[0][0] as { pcm: ArrayBuffer; rms: number };
    expect(pcm.byteLength).toBe(640 * 2);
    expect(rms).toBeGreaterThan(0.3);
    expect(rms).toBeLessThan(0.4);
  });
});

describe("capture worklet in the production build", () => {
  it("is still self-contained after minification (no bundler helpers leak in)", async () => {
    // Same transform as `vite build`: bundle + minify for ES2020.
    const result = await build({
      entryPoints: ["src/audio/captureWorklet.ts"],
      bundle: true,
      minify: true,
      format: "esm",
      target: "es2020",
      write: false,
    });
    const code = result.outputFiles[0].text;
    const mod = await import(/* @vite-ignore */ `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
    const registered: Record<string, new (o: unknown) => { process: (i: Float32Array[][]) => boolean; port: { postMessage: ReturnType<typeof vi.fn> } }> = {};
    class FakeProcessor {
      port = { postMessage: vi.fn() };
    }
    new Function("AudioWorkletProcessor", "registerProcessor", "sampleRate", mod.buildWorkletSource())(
      FakeProcessor,
      (name: string, ctor: (typeof registered)[string]) => (registered[name] = ctor),
      48000
    );
    const processor = new registered[mod.PROCESSOR_NAME]({ processorOptions: { targetRate: 16000, chunkSamples: 640 } });
    const block = sine(48000, 440, 128 / 48000);
    for (let i = 0; i < 20; i++) processor.process([[block]]);
    expect(processor.port.postMessage).toHaveBeenCalledTimes(1);
  });
});

describe("PcmPlayer", () => {
  class FakeSource {
    buffer: { duration: number } | null = null;
    onended: (() => void) | null = null;
    startedAt = -1;
    connect() {}
    start(at: number) {
      this.startedAt = at;
    }
    stop() {}
  }

  function fakeContext() {
    const sources: FakeSource[] = [];
    const ctx = {
      currentTime: 10,
      state: "running",
      destination: {},
      createAnalyser: () => ({ fftSize: 256, connect() {}, getByteTimeDomainData(a: Uint8Array) { a.fill(128); } }),
      createBuffer: (_channels: number, length: number, rate: number) => ({ duration: length / rate, copyToChannel() {} }),
      createBufferSource: () => {
        const s = new FakeSource();
        sources.push(s);
        return s;
      },
      close: async () => {},
      resume: async () => {},
    };
    return { ctx: ctx as unknown as AudioContext, sources };
  }

  function chunk(seconds: number, rate = 22050): ArrayBuffer {
    return new Int16Array(Math.round(seconds * rate)).buffer;
  }

  it("schedules sentences back to back (gapless)", () => {
    const { ctx, sources } = fakeContext();
    const player = new PcmPlayer(ctx);
    player.enqueue(1, chunk(1), 22050);
    player.enqueue(1, chunk(0.5), 22050);
    expect(sources[0].startedAt).toBeCloseTo(10.04);
    expect(sources[1].startedAt).toBeCloseTo(11.04);
  });

  it("reports done only after the server's end marker and the last chunk", () => {
    const { ctx, sources } = fakeContext();
    const player = new PcmPlayer(ctx);
    const done = vi.fn();
    player.onDone = done;
    player.enqueue(3, chunk(0.2), 22050);
    sources[0].onended?.();
    expect(done).not.toHaveBeenCalled();
    player.enqueue(3, chunk(0.2), 22050);
    player.markEnd(3);
    expect(done).not.toHaveBeenCalled();
    sources[1].onended?.();
    expect(done).toHaveBeenCalledWith(3);
  });

  it("stop() silences everything immediately (barge-in)", () => {
    const { ctx, sources } = fakeContext();
    const player = new PcmPlayer(ctx);
    sources.length = 0;
    player.enqueue(1, chunk(1), 22050);
    const stop = vi.spyOn(sources[0], "stop");
    player.stop();
    expect(stop).toHaveBeenCalled();
    expect(player.playing).toBe(false);
  });

  it("converts PCM16 to float", () => {
    const pcm = new Int16Array([0, 16384, -32768]).buffer;
    expect(Array.from(pcm16ToFloat32(pcm))).toEqual([0, 0.5, -1]);
  });
});
