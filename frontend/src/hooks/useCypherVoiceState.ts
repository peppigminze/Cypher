import { useCallback, useEffect, useRef, useState } from "react";
import { speakText, toolChat, transcribeAudio } from "../services/api";

export type CypherVoiceState = "idle" | "listening" | "thinking" | "speaking";

export interface LastTask {
  tool: string;
  success: boolean;
}

/**
 * State machine + plumbing for the full-screen voice UI
 * (CypherVoiceView.tsx / CypherBubble.tsx): idle -> listening (mic
 * recording) -> thinking (/transcribe, then /api/tool-chat) -> speaking
 * (/speak audio playback) -> back to idle.
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
 *
 * KNOWN GAP: /api/tool-chat can come back with `pending_confirmation`
 * set (the backend's write_file approval gate - see
 * backend/app/tools/pending_approvals.py) instead of a final answer.
 * This hook does not currently detect or act on that - `chatResult.reply`
 * (the spoken confirmation prompt, e.g. "Bestätigst du das?") just gets
 * sent to /speak like any other reply, and there is no way to answer it
 * from here; the pipeline returns to idle afterwards without the tool
 * ever running. See /TODO.md for what's needed to close this properly.
 */
export function useCypherVoiceState() {
  const [state, setState] = useState<CypherVoiceState>("idle");
  const [transcript, setTranscript] = useState("");
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lastTask, setLastTask] = useState<LastTask | null>(null);
  const [micStream, setMicStream] = useState<MediaStream | null>(null);

  const [isSupported] = useState(
    () => typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && "MediaRecorder" in window
  );

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const aliveRef = useRef(true);

  const runPipeline = useCallback(async (blob: Blob) => {
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
      setReply(chatResult.reply);
      const lastCall = chatResult.tool_calls[chatResult.tool_calls.length - 1];
      setLastTask(lastCall ? { tool: lastCall.name, success: lastCall.success } : null);

      setState("speaking");
      const spoken = await speakText(chatResult.reply);
      if (!aliveRef.current) return;

      const url = URL.createObjectURL(spoken.audioBlob);
      const audioEl = new Audio(url);
      audioElRef.current = audioEl;
      const finish = () => {
        URL.revokeObjectURL(url);
        if (aliveRef.current) setState("idle");
      };
      audioEl.onended = finish;
      audioEl.onerror = finish;
      await audioEl.play();
    } catch (err) {
      if (!aliveRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
      setState("idle");
    }
  }, []);

  const startListening = useCallback(async () => {
    setError(null);
    setTranscript("");
    setReply("");
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
        void runPipeline(blob);
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setState("listening");
    } catch {
      setError("Mikrofonzugriff verweigert oder nicht verfügbar.");
      setState("idle");
    }
  }, [runPipeline]);

  const stopListening = useCallback(() => {
    mediaRecorderRef.current?.stop();
  }, []);

  const toggleListening = useCallback(() => {
    if (!isSupported) return;
    if (state === "idle") {
      void startListening();
    } else if (state === "listening") {
      stopListening();
    }
    // thinking/speaking: ignore taps for now - can't cleanly interrupt
    // mid-request yet, kept simple for this first cut.
  }, [state, isSupported, startListening, stopListening]);

  const cancel = useCallback(() => {
    audioElRef.current?.pause();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setMicStream(null);
    setState("idle");
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
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
  }, []);

  return { state, transcript, reply, error, lastTask, micStream, isSupported, toggleListening, cancel };
}
