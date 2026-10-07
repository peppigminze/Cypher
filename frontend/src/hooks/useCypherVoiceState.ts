import { useCallback, useEffect, useRef, useState } from "react";
import { confirmToolCall, getActiveProvider, speakText, toolChat, transcribeAudio } from "../services/api";
import type { LlmProviderName, ToolChatResult } from "../types";

export type CypherVoiceState = "idle" | "listening" | "thinking" | "confirming" | "speaking";

export interface LastTask {
  tool: string;
  success: boolean;
}

export interface PendingConfirmation {
  approvalId: string;
  toolName: string;
  arguments: Record<string, unknown>;
}

// Must stay <= the backend's PENDING_TTL_SECONDS
// (backend/app/tools/pending_approvals.py) - this is a client-side
// mirror so the UI doesn't sit on a "confirming" prompt forever if the
// user walks away; the backend enforces the real expiry independently.
const CONFIRMATION_TTL_MS = 5 * 60 * 1000;

const YES_WORDS = /\b(ja|jep|jup|jawohl|jo|bestätige|bestätigen|bestätigt|mach das|mach es|mach's|leg los|ok|okay|yes|yep)\b/i;
const NO_WORDS = /\b(nein|nope|no|abbrechen|abbruch|abgebrochen|stopp|stop|lass es|lass'?s|nicht)\b/i;

/** Simple keyword match, not NLU - "ja"/"nein" and a few common
 * variants. Returns null if the text matches neither (or both).
 * Exported for direct unit testing. */
export function matchYesNo(text: string): boolean | null {
  const isYes = YES_WORDS.test(text);
  const isNo = NO_WORDS.test(text);
  if (isYes && !isNo) return true;
  if (isNo && !isYes) return false;
  return null;
}

/**
 * State machine + plumbing for the full-screen voice UI
 * (CypherVoiceView.tsx / CypherBubble.tsx):
 *   idle -> listening (mic recording) -> thinking (/transcribe, then
 *   /api/tool-chat) -> speaking (/speak audio playback) -> back to idle.
 *
 * If /api/tool-chat comes back with a pending_confirmation (the
 * backend's write_file approval gate - see
 * backend/app/tools/pending_approvals.py), the machine instead goes to
 * confirming: Cypher speaks the confirmation prompt (fire-and-forget,
 * doesn't block the UI) while two ways to answer are offered at once -
 * the Ja/Nein buttons a view renders from `pendingConfirmation` +
 * `confirmPending()`, or tapping the bubble again to speak "ja"/"nein"
 * (simple keyword matching, see matchYesNo - not full NLU). Either path
 * calls POST /api/tool-chat/confirm and resumes the normal
 * thinking -> speaking flow from its result. An unrecognized voice
 * answer, or the TTL expiring, drops cleanly back to idle/confirming
 * without ever leaving a zombie pending state.
 *
 * Push-to-talk, same interaction model as the stt-test.html/tts-test.html
 * reference pages this is built from: tap to start recording, tap again
 * to stop and send. No voice-activity/silence auto-stop (yet) - kept
 * simple and predictable for a first cut.
 *
 * Deliberately independent of useVoiceInput.ts/useSpeechOutput.ts (the
 * browser-native STT/TTS hooks ChatView still uses) - this talks to
 * Cypher's own local STT/TTS/tool-calling endpoints instead, and touches
 * none of that existing wiring.
 */
export function useCypherVoiceState() {
  const [state, setState] = useState<CypherVoiceState>("idle");
  const [transcript, setTranscript] = useState("");
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lastTask, setLastTask] = useState<LastTask | null>(null);
  const [micStream, setMicStream] = useState<MediaStream | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<PendingConfirmation | null>(null);
  // Which provider actually answered the last /api/tool-chat request
  // (Phase 7) - null until the first response comes back. Drives the
  // Lokal/Cloud badge in CypherVoiceView.tsx (requirement: the active
  // provider must always be visible, never a silent cloud switch).
  const [provider, setProvider] = useState<LlmProviderName | null>(null);

  const [isSupported] = useState(
    () => typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && "MediaRecorder" in window
  );

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const confirmationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);

  const clearConfirmationTimeout = useCallback(() => {
    if (confirmationTimeoutRef.current !== null) {
      clearTimeout(confirmationTimeoutRef.current);
      confirmationTimeoutRef.current = null;
    }
  }, []);

  const armConfirmationTimeout = useCallback(() => {
    clearConfirmationTimeout();
    confirmationTimeoutRef.current = setTimeout(() => {
      if (!aliveRef.current) return;
      setPendingConfirmation(null);
      setError("Bestätigung abgelaufen.");
      setState("idle");
    }, CONFIRMATION_TTL_MS);
  }, [clearConfirmationTimeout]);

  /** Fetch /speak and play the result. `thenIdle` (default true) governs
   * whether finishing playback returns the machine to idle - false for
   * the confirmation-prompt announcement, which must leave the machine
   * in "confirming" (with its buttons already interactive) rather than
   * snapping back to idle the moment the prompt finishes speaking. */
  const speakReply = useCallback(async (text: string, opts?: { thenIdle?: boolean }) => {
    const thenIdle = opts?.thenIdle ?? true;
    try {
      const spoken = await speakText(text);
      if (!aliveRef.current) return;
      const url = URL.createObjectURL(spoken.audioBlob);
      const audioEl = new Audio(url);
      audioElRef.current = audioEl;
      const finish = () => {
        URL.revokeObjectURL(url);
        if (aliveRef.current && thenIdle) setState("idle");
      };
      audioEl.onended = finish;
      audioEl.onerror = finish;
      await audioEl.play();
    } catch (err) {
      if (!aliveRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
      if (thenIdle) setState("idle");
    }
  }, []);

  /** Shared tail end of both the initial /api/tool-chat call and the
   * /api/tool-chat/confirm follow-up: record the reply/last tool call,
   * then either pause for confirmation again (chained approvals - not
   * expected in practice today, but handled the same way regardless)
   * or speak the final answer and return to idle. */
  const handleChatResult = useCallback(
    async (chatResult: ToolChatResult) => {
      setReply(chatResult.reply);
      setProvider(chatResult.provider);
      const lastCall = chatResult.tool_calls[chatResult.tool_calls.length - 1];
      setLastTask(lastCall ? { tool: lastCall.name, success: lastCall.success } : null);

      if (chatResult.pending_confirmation) {
        const info = chatResult.pending_confirmation;
        setPendingConfirmation({ approvalId: info.approval_id, toolName: info.tool_name, arguments: info.arguments });
        setState("confirming");
        armConfirmationTimeout();
        void speakReply(chatResult.reply, { thenIdle: false });
        return;
      }

      setState("speaking");
      await speakReply(chatResult.reply);
    },
    [armConfirmationTimeout, speakReply]
  );

  /** Approve or reject the current pendingConfirmation - called from
   * the view's Ja/Nein buttons, or from a matched voice answer. */
  const resolveConfirmation = useCallback(
    async (approved: boolean) => {
      if (!pendingConfirmation) return;
      clearConfirmationTimeout();
      setPendingConfirmation(null);
      setError(null);
      setState("thinking");
      try {
        const result = await confirmToolCall(pendingConfirmation.approvalId, approved);
        if (!aliveRef.current) return;
        await handleChatResult(result);
      } catch (err) {
        if (!aliveRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setState("idle");
      }
    },
    [pendingConfirmation, clearConfirmationTimeout, handleChatResult]
  );

  const confirmPending = useCallback(
    (approved: boolean) => {
      void resolveConfirmation(approved);
    },
    [resolveConfirmation]
  );

  /** Recording finished while answering a confirmation prompt by voice -
   * transcribe and keyword-match instead of sending it as a new
   * request. An unrecognized answer stays in "confirming" so the user
   * can retry by voice or fall back to the buttons. */
  const handleConfirmationAnswer = useCallback(
    async (blob: Blob) => {
      setState("thinking");
      try {
        const transcribed = await transcribeAudio(blob);
        if (!aliveRef.current) return;
        const decision = matchYesNo(transcribed.text);
        if (decision === null) {
          setError(`Nicht verstanden ("${transcribed.text}"). Sag "ja" oder "nein", oder tippe einen Button.`);
          setState("confirming");
          return;
        }
        await resolveConfirmation(decision);
      } catch (err) {
        if (!aliveRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setState("confirming");
      }
    },
    [resolveConfirmation]
  );

  const runPipeline = useCallback(
    async (blob: Blob) => {
      setState("thinking");
      setError(null);
      try {
        const transcribed = await transcribeAudio(blob);
        if (!aliveRef.current) return;
        if (!transcribed.text.trim()) {
          setState("idle");
          return;
        }
        setTranscript(transcribed.text);

        const chatResult = await toolChat(transcribed.text);
        if (!aliveRef.current) return;
        await handleChatResult(chatResult);
      } catch (err) {
        if (!aliveRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setState("idle");
      }
    },
    [handleChatResult]
  );

  const startListening = useCallback(
    async (answeringConfirmation: boolean) => {
      if (!answeringConfirmation) {
        setError(null);
        setTranscript("");
        setReply("");
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        setMicStream(stream);
        chunksRef.current = [];

        const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunksRef.current.push(e.data);
        };
        recorder.onstop = () => {
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
          setMicStream(null);
          const blob = new Blob(chunksRef.current, { type: "audio/webm" });
          if (answeringConfirmation) {
            void handleConfirmationAnswer(blob);
          } else {
            void runPipeline(blob);
          }
        };

        mediaRecorderRef.current = recorder;
        recorder.start();
        setState("listening");
      } catch {
        setError("Mikrofonzugriff verweigert oder nicht verfügbar.");
        setState(answeringConfirmation ? "confirming" : "idle");
      }
    },
    [runPipeline, handleConfirmationAnswer]
  );

  const stopListening = useCallback(() => {
    mediaRecorderRef.current?.stop();
  }, []);

  const toggleListening = useCallback(() => {
    if (!isSupported) return;
    if (state === "idle") {
      void startListening(false);
    } else if (state === "confirming") {
      void startListening(true);
    } else if (state === "listening") {
      stopListening();
    }
    // thinking/speaking: ignore taps for now - can't cleanly interrupt
    // mid-request yet, kept simple for this first cut.
  }, [state, isSupported, startListening, stopListening]);

  const cancel = useCallback(() => {
    clearConfirmationTimeout();
    setPendingConfirmation(null);
    audioElRef.current?.pause();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setMicStream(null);
    setState("idle");
  }, [clearConfirmationTimeout]);

  // Show the correct badge immediately on load, not just after the
  // first request - fetch the currently-active provider once up front.
  // Silently ignored on failure (badge just stays unset until the first
  // real /api/tool-chat response sets it).
  useEffect(() => {
    getActiveProvider()
      .then((info) => {
        if (aliveRef.current) setProvider(info.provider);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      clearConfirmationTimeout();
      // Detach handlers before stopping so an in-flight recording doesn't
      // kick off a fetch pipeline after this component is gone.
      const recorder = mediaRecorderRef.current;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        if (recorder.state !== "inactive") recorder.stop();
      }
      streamRef.current?.getTracks().forEach((t) => t.stop());
      audioElRef.current?.pause();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    state,
    transcript,
    reply,
    error,
    lastTask,
    micStream,
    pendingConfirmation,
    provider,
    isSupported,
    toggleListening,
    confirmPending,
    cancel,
  };
}
