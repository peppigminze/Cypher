/**
 * Live voice session for the voice view: WebSocket client + microphone
 * capture (AudioWorklet, 16 kHz PCM16) + gapless playback, wired to the
 * pure state machine in voiceReducer.ts.
 *
 * Replaces the old request/response pipeline (record a WebM blob ->
 * /api/transcribe -> /api/tool-chat -> /api/speak), which could only
 * start speaking after the whole answer existed and couldn't be
 * interrupted. Now the server detects the end of speech itself (VAD),
 * streams the answer sentence by sentence, and a tap or "Hey Cypher"
 * cuts Cypher off immediately.
 */
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef } from "react";
import { startMicCapture, type MicCapture } from "../audio/captureWorklet";
import { PcmPlayer } from "../audio/pcmPlayer";
import { VoiceClient } from "../services/voiceClient";
import { commandFor, isExpectedTransition, type UiInput } from "./voiceMachine";
import { initialVoiceState, shouldStreamAudio, voiceReducer, type ServerVoiceState, type VoiceMode } from "./voiceReducer";

export function micErrorMessage(err: unknown): string {
  if (typeof navigator !== "undefined" && !navigator.mediaDevices?.getUserMedia) {
    return "Das Mikrofon geht nur über HTTPS oder direkt auf dem PC (localhost). Fürs Handy: siehe docs/MOBILE.md.";
  }
  const name = err instanceof DOMException ? err.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Mikrofonzugriff verweigert. Erlaube das Mikrofon in den Browser-Einstellungen (Schloss-Symbol in der Adressleiste).";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return "Kein Mikrofon gefunden.";
  if (name === "NotReadableError") return "Das Mikrofon wird gerade von einer anderen App benutzt.";
  return "Das Mikrofon konnte nicht gestartet werden.";
}

export function useVoiceSession() {
  const [state, dispatch] = useReducer(voiceReducer, initialVoiceState);
  // Callbacks from the audio thread and the socket read the latest state here.
  const stateRef = useRef(state);
  useLayoutEffect(() => {
    stateRef.current = state;
  }, [state]);

  const clientRef = useRef<VoiceClient | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const micStartingRef = useRef<Promise<boolean> | null>(null);
  const micLevelRef = useRef(0);
  const pttHeldRef = useRef(false);
  // Audio of turns up to this id is dropped (it was interrupted).
  const droppedTurnRef = useRef(-1);

  const ensurePlayer = useCallback((): PcmPlayer => {
    if (!playerRef.current) {
      const player = new PcmPlayer();
      player.onStarted = (turnId) => clientRef.current?.send({ type: "playback_started", turn_id: turnId });
      player.onDone = (turnId) => clientRef.current?.send({ type: "playback_done", turn_id: turnId });
      playerRef.current = player;
    }
    return playerRef.current;
  }, []);

  useEffect(() => {
    const client = new VoiceClient({
      onStatus: (status) => dispatch({ type: "connection", status }),
      onMessage: (message) => {
        if (message.type === "audio_stop") {
          droppedTurnRef.current = Math.max(droppedTurnRef.current, Number(message.turn_id ?? -1));
          playerRef.current?.stop();
        } else if (message.type === "audio_end" && typeof message.turn_id === "number") {
          playerRef.current?.markEnd(message.turn_id);
        }
        if (message.type === "state" && import.meta.env.DEV) {
          const from = stateRef.current.serverState;
          const to = message.state as ServerVoiceState;
          if (!isExpectedTransition(from, to)) console.warn(`[voice] unerwarteter Zustandswechsel ${from} → ${to}`);
        }
        dispatch({ type: "server", message, now: Date.now() });
      },
      onAudio: (header, pcm) => {
        if (header.turn_id <= droppedTurnRef.current) return;
        ensurePlayer().enqueue(header.turn_id, pcm, header.sample_rate);
      },
    });
    clientRef.current = client;
    // Deferred: React's StrictMode mounts twice in development, and closing a
    // socket that is still connecting logs a browser warning.
    const connectTimer = setTimeout(() => client.connect(), 0);
    return () => {
      clearTimeout(connectTimer);
      client.close();
      clientRef.current = null;
      void micRef.current?.stop();
      micRef.current = null;
      void playerRef.current?.close();
      playerRef.current = null;
    };
  }, [ensurePlayer]);

  const enableMic = useCallback(async (): Promise<boolean> => {
    if (micRef.current) return true;
    if (micStartingRef.current) return micStartingRef.current;
    const starting = (async () => {
      try {
        micRef.current = await startMicCapture((chunk) => {
          micLevelRef.current = micLevelRef.current * 0.6 + Math.min(1, chunk.rms * 4) * 0.4;
          if (shouldStreamAudio(stateRef.current, pttHeldRef.current)) clientRef.current?.sendAudio(chunk.pcm);
        });
        dispatch({ type: "mic", enabled: true });
        return true;
      } catch (err) {
        dispatch({ type: "mic", enabled: false, error: micErrorMessage(err) });
        return false;
      } finally {
        micStartingRef.current = null;
      }
    })();
    micStartingRef.current = starting;
    return starting;
  }, []);

  const disableMic = useCallback(async () => {
    const mic = micRef.current;
    micRef.current = null;
    micLevelRef.current = 0;
    await mic?.stop();
    dispatch({ type: "mic", enabled: false });
  }, []);

  const interrupt = useCallback(() => {
    playerRef.current?.stop();
    droppedTurnRef.current = Math.max(droppedTurnRef.current, stateRef.current.turnId);
    clientRef.current?.send({ type: "interrupt" });
    dispatch({ type: "local_interrupt" });
  }, []);

  /** The bubble: one control for everything, depending on the state. */
  const approve = useCallback((approved: boolean) => {
    const card = stateRef.current.approval;
    if (card) clientRef.current?.send({ type: "approval", approval_id: card.approvalId, approved });
  }, []);

  /** Bubble tap, Space, Escape, J/N - what they do per state is the
   * transition table in voiceMachine.ts. */
  const input = useCallback(
    async (event: UiInput) => {
      const current = stateRef.current;
      const command = commandFor(current.serverState, event);
      switch (command) {
        case "start":
          void ensurePlayer().resume();
          if (!(await enableMic())) return;
          pttHeldRef.current = stateRef.current.mode === "ptt";
          clientRef.current?.send({ type: "start" });
          break;
        case "stop":
          pttHeldRef.current = false;
          clientRef.current?.send({ type: "stop" });
          break;
        case "interrupt":
          interrupt();
          break;
        case "approve":
        case "reject":
          approve(command === "approve");
          break;
        case "none":
          break;
      }
    },
    [approve, enableMic, ensurePlayer, interrupt]
  );

  const tap = useCallback(() => input("TAP"), [input]);

  const setMode = useCallback(
    (mode: VoiceMode) => {
      clientRef.current?.send({ type: "mode", mode });
      if (mode === "wake") {
        void ensurePlayer().resume();
        void enableMic();
      }
    },
    [enableMic, ensurePlayer]
  );

  const sendText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      void ensurePlayer().resume();
      playerRef.current?.stop();
      droppedTurnRef.current = Math.max(droppedTurnRef.current, stateRef.current.turnId);
      clientRef.current?.send({ type: "text", text: trimmed });
    },
    [ensurePlayer]
  );

  /** 0..1 loudness for the bubble: Cypher's voice while speaking, else the mic. */
  const getLevel = useCallback((): number => {
    if (stateRef.current.serverState === "speaking") return playerRef.current?.level() ?? 0;
    return micRef.current ? micLevelRef.current : 0;
  }, []);

  /** Fills `bins` with the current spectrum (Cypher's voice while it speaks,
   * else the mic) and returns the loudness 0..1. All zeros when silent. */
  const getSpectrum = useCallback((bins: Uint8Array<ArrayBuffer>): number => {
    const speaking = stateRef.current.serverState === "speaking";
    const analyser = speaking ? (playerRef.current?.playing ? playerRef.current.analyser : null) : (micRef.current?.analyser ?? null);
    if (!analyser) {
      bins.fill(0);
      return 0;
    }
    analyser.getByteFrequencyData(bins);
    return speaking ? (playerRef.current?.level() ?? 0) : micLevelRef.current;
  }, []);

  const dismissError = useCallback(() => dispatch({ type: "dismiss_error" }), []);

  /** Forget the short-term context (memory facts stay). */
  const newConversation = useCallback(() => {
    playerRef.current?.stop();
    droppedTurnRef.current = Math.max(droppedTurnRef.current, stateRef.current.turnId);
    clientRef.current?.send({ type: "new_conversation" });
    dispatch({ type: "server", message: { type: "conversation_reset" }, now: Date.now() });
  }, []);

  return {
    state,
    tap,
    input,
    interrupt,
    setMode,
    approve,
    sendText,
    enableMic,
    disableMic,
    getLevel,
    getSpectrum,
    dismissError,
    newConversation,
  };
}
