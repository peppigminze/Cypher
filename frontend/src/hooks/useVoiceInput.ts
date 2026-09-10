import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Push-to-talk speech-to-text via the browser's native Web Speech API
 * (project spec section 25: voice must be optional and never listen
 * continuously without explicit activation - this only listens between
 * start() and the recognizer's own end event / an explicit stop()).
 *
 * Honest limitation, not hidden: Chrome/Edge's SpeechRecognition sends
 * audio to the browser vendor's own speech service for transcription -
 * it is NOT Cypher's local pipeline. This is the real, working, V1
 * shape of voice input (zero new backend dependencies, works today);
 * see README's Voice section for the local-STT alternative this could
 * grow into later.
 *
 * Requires a secure context (https, or localhost) and a Chromium-based
 * browser - Firefox and Safari don't implement SpeechRecognition.
 */

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
};

function getSpeechRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function useVoiceInput() {
  const [isSupported] = useState(() => getSpeechRecognitionCtor() !== null);
  const [isListening, setIsListening] = useState(false);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const onResultRef = useRef<((finalText: string) => void) | null>(null);

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
    };
  }, []);

  const start = useCallback((onFinalResult: (text: string) => void) => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      setError("Spracherkennung wird von diesem Browser nicht unterstützt.");
      return;
    }
    setError(null);
    onResultRef.current = onFinalResult;

    const recognition = new Ctor();
    recognition.lang = navigator.language || "de-DE";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          final += result[0].transcript;
        } else {
          interim += result[0].transcript;
        }
      }
      setInterimTranscript(interim);
      if (final.trim()) {
        onResultRef.current?.(final.trim());
      }
    };

    recognition.onerror = (event: any) => {
      const message =
        event.error === "not-allowed"
          ? "Mikrofon-Zugriff verweigert."
          : event.error === "no-speech"
            ? "Keine Sprache erkannt."
            : `Spracherkennung fehlgeschlagen (${event.error}).`;
      setError(message);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
      setInterimTranscript("");
    };

    recognitionRef.current = recognition;
    setIsListening(true);
    recognition.start();
  }, []);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);

  return { isSupported, isListening, interimTranscript, error, start, stop };
}
