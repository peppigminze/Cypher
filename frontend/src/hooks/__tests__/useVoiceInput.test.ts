import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useVoiceInput } from "../useVoiceInput";

function installFakeSpeechRecognition() {
  const instances: any[] = [];
  // A plain constructor function, NOT `new FakeClass()` copied via
  // Object.assign - the latter binds each method's `this` to the
  // throwaway instance used to build the copy, not to the real one,
  // so e.g. `stop()` would fire the wrong instance's `onend`.
  const Ctor = vi.fn(function (this: any) {
    this.lang = "";
    this.continuous = false;
    this.interimResults = false;
    this.onresult = null;
    this.onerror = null;
    this.onend = null;
    this.start = vi.fn();
    this.stop = vi.fn(() => this.onend?.());
    this.abort = vi.fn();
    instances.push(this);
  });
  (globalThis as any).SpeechRecognition = Ctor;
  return instances;
}

describe("useVoiceInput", () => {
  afterEach(() => {
    delete (globalThis as any).SpeechRecognition;
    delete (globalThis as any).webkitSpeechRecognition;
  });

  it("reports unsupported when no SpeechRecognition constructor exists", () => {
    const { result } = renderHook(() => useVoiceInput());
    expect(result.current.isSupported).toBe(false);
  });

  it("starts listening and reports a final transcript", () => {
    const instances = installFakeSpeechRecognition();
    const { result } = renderHook(() => useVoiceInput());
    const onFinal = vi.fn();

    act(() => result.current.start(onFinal));
    expect(result.current.isListening).toBe(true);

    const recognition = instances[0];
    const finalResult = Object.assign([{ transcript: "Hallo Cypher" }], { isFinal: true });
    act(() => {
      recognition.onresult?.({ resultIndex: 0, results: [finalResult] });
    });

    expect(onFinal).toHaveBeenCalledWith("Hallo Cypher");
  });

  it("stop() ends the listening session", () => {
    const instances = installFakeSpeechRecognition();
    const { result } = renderHook(() => useVoiceInput());

    act(() => result.current.start(vi.fn()));
    act(() => result.current.stop());

    expect(instances[0].stop).toHaveBeenCalled();
    expect(result.current.isListening).toBe(false);
  });

  it("surfaces a not-allowed error as a readable message", () => {
    const instances = installFakeSpeechRecognition();
    const { result } = renderHook(() => useVoiceInput());

    act(() => result.current.start(vi.fn()));
    act(() => instances[0].onerror?.({ error: "not-allowed" }));

    expect(result.current.error).toContain("Mikrofon-Zugriff verweigert");
    expect(result.current.isListening).toBe(false);
  });
});
