import { useEffect, useState } from "react";
import { getLogs, type LogEntry } from "../../services/api";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;

/** The newest lines of logs/cypher.log (/api/logs) - for "what just happened?". */
export function LogsPanel() {
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("INFO");
  const [entries, setEntries] = useState<LogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let alive = true;
    getLogs(level, 300)
      .then((e) => alive && setEntries([...e].reverse()))
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [level, version]);

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
        <div className="segmented" role="radiogroup" aria-label="Mindest-Level">
          {LEVELS.map((l) => (
            <button
              key={l}
              type="button"
              role="radio"
              aria-checked={level === l}
              className={level === l ? "is-active" : ""}
              onClick={() => setLevel(l)}
            >
              {l}
            </button>
          ))}
        </div>
        <button type="button" className="btn-secondary" onClick={() => setVersion((v) => v + 1)}>
          Aktualisieren
        </button>
      </div>
      {entries === null ? (
        <p className="panel-loading">Lade Logs …</p>
      ) : entries.length === 0 ? (
        <p className="panel-loading">Keine Einträge auf diesem Level.</p>
      ) : (
        <ol className="log-list">
          {entries.map((e, i) => (
            <li key={`${e.ts}-${i}`} className={`log-list__item is-${e.level.toLowerCase()}`}>
              <time dateTime={e.ts}>{new Date(e.ts).toLocaleTimeString("de-CH")}</time>
              <span className="log-list__level">{e.level}</span>
              <span className="log-list__logger">{e.logger.replace(/^cypher\./, "")}</span>
              <span className="log-list__msg">{e.msg}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
