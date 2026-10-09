import { useEffect, useState } from "react";
import { getVoicePrefs, setVoicePrefs } from "../services/api";

const CHOICES = [0, 5, 10, 15, 20, 30];

/** "Weiter zuhören": the follow-up window after an answer (wake mode). */
export function VoicePrefsSection() {
  const [seconds, setSeconds] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getVoicePrefs()
      .then((prefs) => alive && setSeconds(prefs.followup_window_seconds))
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, []);

  async function change(next: number) {
    setError(null);
    try {
      setSeconds((await setVoicePrefs({ followup_window_seconds: next })).followup_window_seconds);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const options = seconds !== null && !CHOICES.includes(seconds) ? [...CHOICES, seconds].sort((a, b) => a - b) : CHOICES;
  return (
    <section className="settings__section">
      <h2>Weiter zuhören</h2>
      <p className="settings__hint">
        Im Modus „Hey Cypher“ hört Cypher nach jeder Antwort noch eine Weile zu: Folgefragen ohne Aktivierungswort. Der Ring um den Kern
        zeigt, wie lange noch. „Cypher, schlaf“ beendet das sofort.
      </p>
      <label className="settings__field">
        <span>Nach einer Antwort</span>
        <select
          className="select-input"
          value={seconds ?? ""}
          disabled={seconds === null}
          onChange={(e) => void change(Number(e.target.value))}
          aria-label="Weiter zuhören nach einer Antwort"
        >
          {options.map((s) => (
            <option key={s} value={s}>
              {s === 0 ? "aus" : `${s} Sekunden`}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p className="settings__hint settings__hint--error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
