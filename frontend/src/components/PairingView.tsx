import { useState } from "react";
import { pairWithCode } from "../services/auth";

interface Props {
  offline: boolean;
  onPaired: () => void;
  onRetry: () => void;
}

/**
 * Shown when this device has no valid token: either the backend is not
 * reachable, or this is another device (phone) that still needs pairing.
 */
export function PairingView({ offline, onPaired, onRetry }: Props) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const ok = await pairWithCode(code);
    setBusy(false);
    if (ok) onPaired();
    else setError("Der Code passt nicht. Prüfe ihn in der Cypher-Oberfläche am PC (Menü → Gerät koppeln).");
  };

  if (offline) {
    return (
      <main className="pairing" aria-live="polite">
        <h1>Cypher ist nicht erreichbar</h1>
        <p>
          Das Backend antwortet nicht. Läuft Cypher auf dem PC? Starte es mit <code>start_cypher.bat</code>.
        </p>
        <p>Vom Handy aus: Ist Tailscale auf beiden Geräten aktiv?</p>
        <button type="button" className="pairing__button" onClick={onRetry}>
          Erneut versuchen
        </button>
      </main>
    );
  }

  return (
    <main className="pairing">
      <h1>Gerät koppeln</h1>
      <p>
        Dieses Gerät ist noch nicht mit Cypher verbunden. Öffne Cypher am PC, wähle im Menü <strong>Gerät koppeln</strong> und öffne dort
        den Link auf diesem Gerät – oder gib den Code hier ein.
      </p>
      <form onSubmit={submit} className="pairing__form">
        <label htmlFor="pairing-code">Kopplungscode</label>
        <input
          id="pairing-code"
          type="password"
          autoComplete="off"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="Code einfügen"
          required
        />
        <button type="submit" className="pairing__button" disabled={busy || !code.trim()}>
          {busy ? "Prüfe …" : "Koppeln"}
        </button>
      </form>
      {error && (
        <p className="pairing__error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}
