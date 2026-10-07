import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { matchYesNo, useCypherVoiceState } from "../useCypherVoiceState";
import * as api from "../../services/api";
import type { ToolChatResult, TranscribeResult } from "../../types";

vi.mock("../../services/api");

// --- Fake MediaRecorder / getUserMedia / Audio -----------------------------
// Same spirit as useVoiceInput.test.ts's fake SpeechRecognition: enough of
// the real browser API surface to drive the hook's recording lifecycle
// under our control, not a full media stack.

function installFakeMediaStack() {
  const recorders: any[] = [];
  const audios: any[] = [];

  const fakeTrack = { stop: vi.fn() };
  const fakeStream = { getTracks: () => [fakeTrack] } as unknown as MediaStream;

  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue(fakeStream) },
  });

  class FakeMediaRecorder {
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    state: "inactive" | "recording" = "inactive";
    constructor(public stream: MediaStream) {
      recorders.push(this);
    }
    start() {
      this.state = "recording";
    }
    // Synchronously emits one chunk and fires onstop - a real recorder is
    // async, but the hook only cares about the eventual callbacks.
    stop() {
      this.state = "inactive";
      this.ondataavailable?.({ data: new Blob(["fake-audio"], { type: "audio/webm" }) });
      this.onstop?.();
    }
  }
  (globalThis as any).MediaRecorder = FakeMediaRecorder;

  class FakeAudio {
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public src: string) {
      audios.push(this);
    }
    play() {
      return Promise.resolve();
    }
    pause() {}
  }
  (globalThis as any).Audio = FakeAudio;

  URL.createObjectURL = vi.fn(() => "blob:fake");
  URL.revokeObjectURL = vi.fn();

  return { recorders, audios };
}

function makeTranscribeResult(text: string): TranscribeResult {
  return { text, language: "de", audio_duration_seconds: 1, transcribe_time_seconds: 0.1 };
}

function makePendingResult(reply: string): ToolChatResult {
  return {
    reply,
    tool_calls: [],
    steps_used: 1,
    pending_confirmation: { approval_id: "approval-1", tool_name: "write_file", arguments: { path: "notiz.txt" } },
    provider: "local",
  };
}

function makeFinalResult(reply: string, toolName = "write_file", success = true): ToolChatResult {
  return {
    reply,
    tool_calls: [{ name: toolName, arguments: {}, success, error: success ? null : "failed" }],
    steps_used: 2,
    pending_confirmation: null,
    provider: "local",
  };
}

describe("matchYesNo", () => {
  it("recognizes common yes phrasings", () => {
    expect(matchYesNo("Ja")).toBe(true);
    expect(matchYesNo("ja, mach das")).toBe(true);
    expect(matchYesNo("Okay")).toBe(true);
  });

  it("recognizes common no phrasings", () => {
    expect(matchYesNo("Nein")).toBe(false);
    expect(matchYesNo("nein, abbrechen")).toBe(false);
    expect(matchYesNo("stopp")).toBe(false);
  });

  it("returns null for ambiguous or unrelated text", () => {
    expect(matchYesNo("vielleicht später")).toBeNull();
    expect(matchYesNo("ja aber nein doch")).toBeNull();
    expect(matchYesNo("")).toBeNull();
  });
});

describe("useCypherVoiceState - confirming state", () => {
  let mediaStack: ReturnType<typeof installFakeMediaStack>;

  beforeEach(() => {
    mediaStack = installFakeMediaStack();
    // The hook fetches the active provider on mount (Phase 7) purely to
    // show the correct badge immediately - irrelevant to these
    // confirming-state tests, so give it a resolved stub.
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: false });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete (globalThis as any).MediaRecorder;
    delete (globalThis as any).Audio;
  });

  // Fire-and-forget async chains (transcribe -> toolChat/confirm -> ...)
  // keep resolving after a plain act() returns - a couple of microtask
  // ticks inside an async act() lets React flush the state updates that
  // land in between, instead of them landing "outside of act(...)".
  const flushPromises = () => act(() => new Promise((resolve) => setTimeout(resolve, 0)));

  async function recordAndStop(result: { current: ReturnType<typeof useCypherVoiceState> }, atRecorderIndex: number) {
    act(() => {
      result.current.toggleListening();
    });
    await waitFor(() => expect(result.current.state).toBe("listening"));
    act(() => {
      mediaStack.recorders[atRecorderIndex].stop();
    });
    await flushPromises();
    await flushPromises();
  }

  it("enters 'confirming' and exposes pendingConfirmation when tool-chat pauses for approval", async () => {
    vi.mocked(api.transcribeAudio).mockResolvedValue(makeTranscribeResult("Erstelle notiz.txt"));
    vi.mocked(api.toolChat).mockResolvedValue(makePendingResult('Ich möchte "write_file" ausführen. Bestätigst du das?'));
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());

    await recordAndStop(result, 0);

    await waitFor(() => expect(result.current.state).toBe("confirming"));
    expect(result.current.pendingConfirmation).toEqual({
      approvalId: "approval-1",
      toolName: "write_file",
      arguments: { path: "notiz.txt" },
    });
    // The prompt is spoken (fire-and-forget) without blocking the confirming state.
    expect(api.speakText).toHaveBeenCalledWith('Ich möchte "write_file" ausführen. Bestätigst du das?');
  });

  it("confirmPending(true) approves via the backend and continues to speaking", async () => {
    vi.mocked(api.transcribeAudio).mockResolvedValue(makeTranscribeResult("Erstelle notiz.txt"));
    vi.mocked(api.toolChat).mockResolvedValue(makePendingResult("Bestätigst du das?"));
    vi.mocked(api.confirmToolCall).mockResolvedValue(makeFinalResult("Erledigt. Datei erstellt."));
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());
    await recordAndStop(result, 0);
    await waitFor(() => expect(result.current.state).toBe("confirming"));

    act(() => {
      result.current.confirmPending(true);
    });
    await flushPromises();
    await flushPromises();

    expect(api.confirmToolCall).toHaveBeenCalledWith("approval-1", true);
    await waitFor(() => expect(result.current.state).toBe("speaking"));
    expect(result.current.pendingConfirmation).toBeNull();
    expect(result.current.reply).toBe("Erledigt. Datei erstellt.");

    // Playback finishing returns the machine to idle.
    act(() => mediaStack.audios[mediaStack.audios.length - 1].onended?.());
    await waitFor(() => expect(result.current.state).toBe("idle"));
  });

  it("confirmPending(false) rejects via the backend instead of running the tool", async () => {
    vi.mocked(api.transcribeAudio).mockResolvedValue(makeTranscribeResult("Erstelle geheim.txt"));
    vi.mocked(api.toolChat).mockResolvedValue(makePendingResult("Bestätigst du das?"));
    vi.mocked(api.confirmToolCall).mockResolvedValue(makeFinalResult("Abgebrochen.", "write_file", false));
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());
    await recordAndStop(result, 0);
    await waitFor(() => expect(result.current.state).toBe("confirming"));

    act(() => {
      result.current.confirmPending(false);
    });
    await flushPromises();
    await flushPromises();

    expect(api.confirmToolCall).toHaveBeenCalledWith("approval-1", false);
    expect(result.current.pendingConfirmation).toBeNull();
  });

  it("a recognized spoken 'ja' while confirming resolves the approval by voice", async () => {
    vi.mocked(api.transcribeAudio)
      .mockResolvedValueOnce(makeTranscribeResult("Erstelle notiz.txt"))
      .mockResolvedValueOnce(makeTranscribeResult("Ja, mach das"));
    vi.mocked(api.toolChat).mockResolvedValue(makePendingResult("Bestätigst du das?"));
    vi.mocked(api.confirmToolCall).mockResolvedValue(makeFinalResult("Erledigt."));
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());
    await recordAndStop(result, 0);
    await waitFor(() => expect(result.current.state).toBe("confirming"));

    // Tap again while confirming - starts a second recording for the spoken answer.
    await recordAndStop(result, 1);

    await waitFor(() => expect(api.confirmToolCall).toHaveBeenCalledWith("approval-1", true));
  });

  it("an unrecognized spoken answer stays in 'confirming' with an error, without calling confirm", async () => {
    vi.mocked(api.transcribeAudio)
      .mockResolvedValueOnce(makeTranscribeResult("Erstelle notiz.txt"))
      .mockResolvedValueOnce(makeTranscribeResult("vielleicht später"));
    vi.mocked(api.toolChat).mockResolvedValue(makePendingResult("Bestätigst du das?"));
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());
    await recordAndStop(result, 0);
    await waitFor(() => expect(result.current.state).toBe("confirming"));

    await recordAndStop(result, 1);

    await waitFor(() => expect(result.current.error).toContain("Nicht verstanden"));
    expect(result.current.state).toBe("confirming");
    expect(result.current.pendingConfirmation).not.toBeNull();
    expect(api.confirmToolCall).not.toHaveBeenCalled();
  });

  it("fetches the active provider on mount so the badge is correct immediately", async () => {
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "cloud", cloud_configured: true });

    const { result } = renderHook(() => useCypherVoiceState());

    await waitFor(() => expect(result.current.provider).toBe("cloud"));
  });

  it("updates provider from the actual /api/tool-chat response, not just the initial fetch", async () => {
    // Started as "local" per the initial fetch, but the request itself
    // says "cloud" answered it (e.g. the user switched providers in
    // Settings between page load and this request).
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: true });
    vi.mocked(api.transcribeAudio).mockResolvedValue(makeTranscribeResult("Wie spät ist es?"));
    vi.mocked(api.toolChat).mockResolvedValue({
      reply: "Es ist 12 Uhr.",
      tool_calls: [],
      steps_used: 1,
      pending_confirmation: null,
      provider: "cloud",
    });
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());
    await waitFor(() => expect(result.current.provider).toBe("local"));

    await recordAndStop(result, 0);

    await waitFor(() => expect(result.current.provider).toBe("cloud"));
  });

  it("cancel() clears an in-flight confirmation and returns to idle", async () => {
    vi.mocked(api.transcribeAudio).mockResolvedValue(makeTranscribeResult("Erstelle notiz.txt"));
    vi.mocked(api.toolChat).mockResolvedValue(makePendingResult("Bestätigst du das?"));
    vi.mocked(api.speakText).mockResolvedValue({ audioBlob: new Blob(), audioDurationSeconds: 1, generationTimeSeconds: 0.1 });

    const { result } = renderHook(() => useCypherVoiceState());
    await recordAndStop(result, 0);
    await waitFor(() => expect(result.current.state).toBe("confirming"));

    act(() => {
      result.current.cancel();
    });

    expect(result.current.state).toBe("idle");
    expect(result.current.pendingConfirmation).toBeNull();
  });
});
