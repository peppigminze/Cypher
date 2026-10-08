/** Spoken answers are read, not rendered: drop Markdown markers the model
 * may use ("**fett**", "* Punkt", "## Titel") for subtitles. Speech gets
 * the same cleanup on the server (app/voice/speech_text.py). */
export function plainText(text: string): string {
  return text
    .replace(/\*\*|__/g, "")
    .replace(/(^|\s)[*•]\s+/g, "$1")
    .replace(/^\s*-\s+/gm, "")
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/`/g, "");
}
