import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Text-to-speech via the browser's native SpeechSynthesis API (project
 * spec section 25). Same honesty note as useVoiceInput: on most
 * platforms the voices are provided by the OS/browser, not a Cypher-run
 * local model - but it works today with zero new dependencies. Speaking
 * is always explicit (a message finishing) or opt-in (the "vorlesen"
 * toggle in Settings), never automatic on page load.
 */
export function useSpeechOutput() {
  const [isSupported] = useState(() => "speechSynthesis" in window);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  useEffect(() => {
    return () => {
      if (isSupported) window.speechSynthesis.cancel();
    };
  }, [isSupported]);

  const speak = useCallback(
    (text: string) => {
      if (!isSupported || !text.trim()) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = navigator.language || "de-DE";
      utterance.onstart = () => setIsSpeaking(true);
      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = () => setIsSpeaking(false);
      utteranceRef.current = utterance;
      window.speechSynthesis.speak(utterance);
    },
    [isSupported]
  );

  const stop = useCallback(() => {
    if (isSupported) window.speechSynthesis.cancel();
    setIsSpeaking(false);
  }, [isSupported]);

  return { isSupported, isSpeaking, speak, stop };
}
