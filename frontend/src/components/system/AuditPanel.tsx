import { useEffect, useMemo, useState } from "react";
import { getAudit, type AuditEntry } from "../../services/api";

const DECISION_LABEL: Record<string, string> = {
  auto: "ausgeführt",
  auto_unconfirmed: "ausgeführt - Abfrage war aus",
  setting_changed: "Einstellung geändert",
  approved: "bestätigt + ausgeführt",
  approval_requested: "Freigabe gefragt",
  rejected: "abgelehnt",
  expired: "Freigabe abgelaufen",
  blocked: "gesperrt",
  tainted_blocked: "verweigert (fremder Inhalt)",
  needs_approval: "ohne Freigabe verweigert",
  invalid: "ungültige Argumente",
  unknown_tool: "unbekanntes Tool",
  error: "Fehler",
};

const FILTERS: { id: string; label: string; match: (e: AuditEntry) => boolean }[] = [
  { id: "all", label: "Alle", match: () => true },
  { id: "approvals", label: "Freigaben", match: (e) => ["approval_requested", "approved", "rejected", "expired"].includes(e.decision) },
  { id: "unconfirmed", label: "Abfrage aus", match: (e) => ["auto_unconfirmed", "setting_changed"].includes(e.decision) },
  {
    id: "refused",
    label: "Verweigert",
    match: (e) => ["blocked", "tainted_blocked", "needs_approval", "invalid", "unknown_tool"].includes(e.decision),
  },
  { id: "failed", label: "Fehlgeschlagen", match: (e) => e.success === false },
];

/** What Cypher did on this PC (or was stopped from doing) - the audit log (/api/audit). */
export function AuditPanel() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let alive = true;
    getAudit(200)
      .then((e) => alive && setEntries(e))
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [version]);

  const shown = useMemo(() => (entries ?? []).filter(FILTERS.find((f) => f.id === filter)!.match), [entries, filter]);

  if (error) {
    return (
      <p className="panel-error" role="alert">
        {error}
      </p>
    );
  }
  return (
    <div className="sys-panel">
      <div className="sys-panel__head">
        <div className="segmented" role="radiogroup" aria-label="Filter">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={filter === f.id}
              className={filter === f.id ? "is-active" : ""}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <button type="button" className="btn-secondary" onClick={() => setVersion((v) => v + 1)}>
          Aktualisieren
        </button>
      </div>
      {entries === null ? (
        <p className="panel-loading">Lade Audit-Log …</p>
      ) : shown.length === 0 ? (
        <p className="panel-loading">Keine Einträge.</p>
      ) : (
        <ol className="audit-list">
          {shown.map((e) => (
            <li key={e.id} className={`audit-list__item is-${e.decision}`}>
              <time dateTime={e.ts}>{new Date(e.ts).toLocaleString("de-CH", { dateStyle: "short", timeStyle: "medium" })}</time>
              <span className="audit-list__tool">{e.tool}</span>
              <span className={`audit-list__decision is-${e.success === false ? "failed" : e.decision}`}>
                {DECISION_LABEL[e.decision] ?? e.decision}
              </span>
              {e.risk && <span className="audit-list__risk">{e.risk}</span>}
              {e.source && (
                <span className="audit-list__source">{e.source === "voice" ? "Sprache" : e.source === "text" ? "Chat" : e.source}</span>
              )}
              {e.summary && <p className="audit-list__summary">{e.summary}</p>}
              {e.error && <p className="audit-list__error">{e.error}</p>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
