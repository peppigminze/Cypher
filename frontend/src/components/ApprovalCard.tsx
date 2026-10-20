import { useEffect, useState } from "react";
import type { ApprovalCard as Card } from "../hooks/voiceReducer";

const RISK_LABEL: Record<string, string> = {
  control: "Steuert etwas am PC",
  write: "Speichert oder ändert Daten",
  critical: "Kritische Aktion",
  destructive: "Kritische Aktion",
  cloud: "Verlässt deinen PC",
};

const KIND_LABEL: Record<string, string> = {
  red_line: "Rote Linie - fragt immer, auch wenn die Sicherheitsabfrage aus ist",
  untrusted: "Der Auftrag stammt (auch) aus fremdem Inhalt (Webseite, Datei, Zwischenablage)",
};

function argumentText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/**
 * "Soll ich das machen?" - shown when a tool call needs the user's approval
 * (backend app/security/policy.py). Shows the tool, the EXACT arguments
 * (e.g. the PowerShell command), the risk tier and why it was rated so.
 * Critical actions: by voice only "Ja, ausführen" counts (shown as a hint).
 * Used by the voice view and the text chat, on the PC and the phone.
 */
export function ApprovalCard({
  card,
  onDecide,
  showKeys = false,
}: {
  card: Card;
  onDecide: (approved: boolean) => void;
  showKeys?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((card.expiresAt - now) / 1000));
  const titleId = `approval-title-${card.approvalId}`;
  const summaryId = `approval-summary-${card.approvalId}`;
  const critical = card.risk === "critical" || card.risk === "destructive";
  const args = Object.entries(card.arguments ?? {});
  return (
    <section
      className={`voice-view__confirm voice-view__confirm--${critical ? "critical" : card.risk}`}
      role="alertdialog"
      aria-labelledby={titleId}
      aria-describedby={summaryId}
    >
      <p id={titleId} className="voice-view__confirm-risk">
        {RISK_LABEL[card.risk] ?? "Braucht deine Bestätigung"}
      </p>
      <p id={summaryId} className="voice-view__confirm-summary">
        {card.summary || card.toolName}
      </p>
      {card.reason && <p className="voice-view__confirm-reason">Grund: {card.reason}</p>}
      {KIND_LABEL[card.kind] && <p className="voice-view__confirm-reason">{KIND_LABEL[card.kind]}</p>}
      {args.length > 0 && (
        <details className="voice-view__confirm-details" open={critical}>
          <summary>Werkzeug {card.toolName} - genaue Argumente</summary>
          <dl>
            {args.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>
                  <code>{argumentText(value)}</code>
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      <div className="voice-view__confirm-actions">
        <button
          type="button"
          autoFocus
          className="voice-view__confirm-btn voice-view__confirm-btn--yes"
          disabled={seconds === 0}
          onClick={() => onDecide(true)}
        >
          {critical ? "Ausführen" : "Ja"}
          {showKeys && <kbd>J</kbd>}
        </button>
        <button
          type="button"
          className="voice-view__confirm-btn voice-view__confirm-btn--no"
          disabled={seconds === 0}
          onClick={() => onDecide(false)}
        >
          Nein{showKeys && <kbd>N</kbd>}
        </button>
      </div>
      {critical && card.voicePhrase !== "Ja" && (
        <p className="voice-view__confirm-voice">Per Sprache: „{card.voicePhrase}“ - ein einfaches „Ja“ reicht nicht.</p>
      )}
      <p className="voice-view__confirm-expiry">{seconds > 0 ? `Läuft in ${seconds} s ab` : "Abgelaufen - sag es einfach nochmal."}</p>
    </section>
  );
}
