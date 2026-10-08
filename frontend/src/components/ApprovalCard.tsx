import { useEffect, useState } from "react";
import type { ApprovalCard as Card } from "../hooks/voiceReducer";

const RISK_LABEL: Record<string, string> = {
  control: "Steuert etwas am PC",
  write: "Speichert oder ändert Daten",
  destructive: "Nicht rückgängig zu machen",
};

/**
 * "Soll ich das machen?" - shown when a tool needs the user's approval
 * (risk tier write/destructive, see backend app/tools/base.py). Used by the
 * voice view and the text chat; the summary comes from the tool itself and
 * names exactly what would happen.
 */
export function ApprovalCard({ card, onDecide, showKeys = false }: { card: Card; onDecide: (approved: boolean) => void; showKeys?: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((card.expiresAt - now) / 1000));
  const titleId = `approval-title-${card.approvalId}`;
  const summaryId = `approval-summary-${card.approvalId}`;
  return (
    <section className={`voice-view__confirm voice-view__confirm--${card.risk}`} role="alertdialog" aria-labelledby={titleId} aria-describedby={summaryId}>
      <p id={titleId} className="voice-view__confirm-risk">
        {RISK_LABEL[card.risk] ?? "Braucht deine Bestätigung"}
      </p>
      <p id={summaryId} className="voice-view__confirm-summary">
        {card.summary || card.toolName}
      </p>
      <div className="voice-view__confirm-actions">
        <button type="button" autoFocus className="voice-view__confirm-btn voice-view__confirm-btn--yes" disabled={seconds === 0} onClick={() => onDecide(true)}>
          Ja{showKeys && <kbd>J</kbd>}
        </button>
        <button type="button" className="voice-view__confirm-btn voice-view__confirm-btn--no" disabled={seconds === 0} onClick={() => onDecide(false)}>
          Nein{showKeys && <kbd>N</kbd>}
        </button>
      </div>
      <p className="voice-view__confirm-expiry">{seconds > 0 ? `Läuft in ${seconds} s ab` : "Abgelaufen - sag es einfach nochmal."}</p>
    </section>
  );
}
