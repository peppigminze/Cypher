import { useState } from "react";
import { useCriticalConfirm } from "../hooks/useCriticalConfirm";
import { setCriticalConfirm } from "../services/api";

const MODE_TEXT = {
  on: "AN - kritische Aktionen fragen vorher nach",
  off_session: "AUS bis zum nächsten Neustart von Cypher",
  off_permanent: "AUS (dauerhaft)",
} as const;

/**
 * "Sicherheitsabfrage für kritische Aktionen" (FIX2). Default ON. Turning it
 * off needs a second, explicit step with a warning and the choice "nur für
 * diese Sitzung" or "dauerhaft". The backend accepts the change ONLY from
 * this page in a browser on the PC itself - not from the phone, not by
 * voice and never from Cypher's own tools - and logs every change.
 */
export function SafetyPromptSection() {
  const [state] = useCriticalConfirm();
  const [warning, setWarning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(enabled: boolean, scope: "session" | "permanent") {
    setBusy(true);
    setError(null);
    try {
      await setCriticalConfirm(enabled, scope);
      setWarning(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings__section" aria-labelledby="safety-prompt-title">
      <h2 id="safety-prompt-title">Sicherheitsabfrage für kritische Aktionen</h2>
      <p className="settings__hint">
        Cypher darf auf diesem PC fast alles. Kritisches - z. B. endgültig löschen, Software installieren, herunterfahren,
        Systemeinstellungen, Passwörter lesen - fragt vorher nach. Alles wird im Audit-Log festgehalten.
      </p>
      <p className={`safety-state ${state && !state.enabled ? "safety-state--off" : ""}`} role="status">
        Status: {state ? MODE_TEXT[state.mode] : "wird geladen …"}
      </p>
      {state && (
        <p className="settings__hint">Fragt immer, auch wenn sie aus ist (rote Linie): {state.red_line_labels.join(", ") || "nichts"}.</p>
      )}

      {state?.enabled && !warning && (
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => setWarning(true)}>
          Ausschalten …
        </button>
      )}
      {state && !state.enabled && (
        <button type="button" className="btn-primary" disabled={busy} onClick={() => void change(true, "permanent")}>
          Wieder einschalten
        </button>
      )}

      {warning && (
        <div className="safety-warning" role="alertdialog" aria-labelledby="safety-warning-title" aria-describedby="safety-warning-text">
          <p id="safety-warning-title" className="safety-warning__title">
            Wirklich ohne Nachfrage?
          </p>
          <p id="safety-warning-text">
            Dann führt Cypher auch kritische Aktionen sofort aus - z. B. Dateien endgültig löschen, Programme installieren oder den PC
            herunterfahren. Ein Missverständnis oder ein manipulierter Text (Webseite, Datei) kann dann echten Schaden anrichten. Was aus
            fremdem Inhalt kommt und die rote Linie fragen weiterhin nach.
          </p>
          <div className="safety-warning__actions">
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => void change(false, "session")}>
              Nur für diese Sitzung
            </button>
            <button
              type="button"
              className="btn-secondary safety-warning__danger"
              disabled={busy}
              onClick={() => void change(false, "permanent")}
            >
              Dauerhaft ausschalten
            </button>
            <button type="button" className="btn-primary" autoFocus disabled={busy} onClick={() => setWarning(false)}>
              Abbrechen
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="settings__hint settings__hint--error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
