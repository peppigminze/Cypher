/** What the voice view says about its state (caption under the bubble, button label). */
import type { VoiceUiState } from "./voiceReducer";

export function caption(state: VoiceUiState): string {
  if (state.connection === "unauthorized") return "Anmeldung abgelaufen - bitte neu koppeln.";
  if (state.connection === "forbidden") return "Diese Adresse ist im Backend nicht freigegeben (CORS_ORIGINS).";
  if (state.connection !== "open")
    return state.connection === "connecting" ? "Verbinde mit Cypher …" : "Verbindung getrennt - ich versuche es weiter …";
  if (state.micError) return state.micError;
  switch (state.serverState) {
    case "wake_listening":
      return state.micEnabled ? `Sag „${state.wakePhrase}“ …` : `Tippen, um „${state.wakePhrase}“ einzuschalten`;
    case "listening":
      if (state.mode === "ptt") return "Sprich - nochmal tippen zum Senden";
      if (state.followup.phase === "open" && !state.userSpeaking) return "Ich höre weiter zu - frag einfach weiter.";
      return state.userSpeaking ? "Ich höre …" : "Ich höre zu …";
    case "thinking":
      return state.tool?.running ? state.tool.summary || "Einen Moment …" : "Denke nach …";
    case "speaking":
      return "Tippen zum Unterbrechen";
    case "confirming":
      // Critical actions: a plain "ja" is not enough (backend app/voice/yesno.py).
      if (state.approval && state.approval.voicePhrase !== "Ja")
        return `Sag „${state.approval.voicePhrase}“ oder „nein“ - oder tippe unten.`;
      return "Sag „ja“ oder „nein“ - oder tippe unten.";
    default:
      return "Tippen zum Sprechen";
  }
}

export function bubbleLabel(state: VoiceUiState): string {
  switch (state.serverState) {
    case "thinking":
    case "speaking":
      return "Cypher unterbrechen";
    case "listening":
      if (state.followup.phase === "open") return "Weiter zuhören beenden";
      return state.mode === "ptt" ? "Aufnahme senden" : "Fertig gesprochen";
    case "confirming":
      return "Antworte mit ja oder nein";
    default:
      return state.micEnabled ? "Sprechen" : "Mikrofon einschalten und sprechen";
  }
}
