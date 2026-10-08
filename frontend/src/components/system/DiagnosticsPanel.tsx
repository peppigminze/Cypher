import { useEffect, useState } from "react";
import { getDiagnostics, type Diagnostics } from "../../services/api";

const COMPONENT_LABEL: Record<string, string> = {
  database: "Datenbank",
  ollama: "Ollama",
  llm_model: "Sprachmodell",
  gpu: "Grafikkarte",
  stt: "Spracherkennung",
  tts: "Sprachausgabe",
};

const METRIC_LABEL: Record<string, string> = {
  e2e_first_audible: "Sprechende → erste Silbe",
  e2e_first_audio: "Sprechende → erstes Audio gesendet",
  turn_first_sentence: "Erster Satz der Antwort",
  turn_llm_first_token: "Erstes Token des Modells",
  turn_total: "Antwort komplett",
  llm_ttft: "Modell: erstes Token",
  llm_total: "Modell: Aufruf gesamt",
  stt: "Spracherkennung",
  stt_wake: "Aktivierungswort prüfen",
  tts: "Sprachausgabe (pro Satz)",
  tool: "Tool-Ausführung",
  http: "HTTP-Anfragen",
};

const BUDGET_LABEL: Record<string, string> = {
  system_prompt: "System-Prompt",
  tool_definitions: "Tool-Beschreibungen",
  core_memory_max: "Kern-Gedächtnis (max.)",
  history_max: "Gesprächsverlauf (max.)",
  recall_max: "Archiv-Treffer (max.)",
  tool_result_max: "Tool-Ergebnis (max.)",
  answer_reserve: "Reserve für die Antwort",
};

function ms(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(2).replace(".", ",")} s` : `${Math.round(value)} ms`;
}

/** Everything the backend knows about itself (/api/diagnostics), readable. */
export function DiagnosticsPanel() {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let alive = true;
    getDiagnostics()
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [version]);

  if (error) {
    return (
      <p className="panel-error" role="alert">
        {error}
      </p>
    );
  }
  if (!data) return <p className="panel-loading">Lade Diagnose …</p>;
  const budget = data.context_budget;
  const metricOrder = Object.keys(METRIC_LABEL);
  const rank = (name: string) => (metricOrder.includes(name) ? metricOrder.indexOf(name) : metricOrder.length);
  const metrics = Object.entries(data.latency).sort(([a], [b]) => rank(a) - rank(b));

  return (
    <div className="sys-panel">
      <div className="sys-panel__head">
        <p className="sys-panel__meta">
          Version {data.version} · läuft seit {Math.round(data.uptime_seconds / 60)} min
        </p>
        <button type="button" className="btn-secondary" onClick={() => setVersion((v) => v + 1)}>
          Aktualisieren
        </button>
      </div>

      <h3>Komponenten</h3>
      <ul className="health-list">
        {Object.entries(data.components).map(([name, c]) => (
          <li key={name} className={`health-list__item is-${c.status}`}>
            <span className="health-list__name">{COMPONENT_LABEL[name] ?? name}</span>
            <span className="health-list__msg">
              {c.message}
              {c.hint && <span className="health-list__hint">{c.hint}</span>}
            </span>
          </li>
        ))}
      </ul>

      <h3>Latenzen (seit dem Start)</h3>
      {metrics.length === 0 ? (
        <p className="panel-loading">Noch keine Messungen - sag etwas zu Cypher.</p>
      ) : (
        <table className="sys-table">
          <thead>
            <tr>
              <th scope="col">Messpunkt</th>
              <th scope="col">Median</th>
              <th scope="col">90 %</th>
              <th scope="col">Zuletzt</th>
              <th scope="col">Anzahl</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map(([name, m]) => (
              <tr key={name}>
                <th scope="row">{METRIC_LABEL[name] ?? name}</th>
                <td>{ms(m.p50_ms)}</td>
                <td>{ms(m.p90_ms)}</td>
                <td>{ms(m.last_ms)}</td>
                <td>{m.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {budget.parts && budget.window && (
        <>
          <h3>Kontextfenster</h3>
          <p className="sys-panel__meta">
            {budget.total} von {budget.window} Tokens im ungünstigsten Fall {budget.fits ? "- passt." : "- zu gross, siehe Log-Warnung."}
          </p>
          <div className="budget-bar" role="img" aria-label="Aufteilung des Kontextfensters">
            {Object.entries(budget.parts).map(([part, tokens]) => (
              <span
                key={part}
                className={`budget-bar__part budget-bar__part--${part}`}
                style={{ width: `${(tokens / budget.window!) * 100}%` }}
                title={`${BUDGET_LABEL[part] ?? part}: ${tokens}`}
              />
            ))}
          </div>
          <ul className="budget-legend">
            {Object.entries(budget.parts).map(([part, tokens]) => (
              <li key={part}>
                <span className={`budget-legend__swatch budget-bar__part--${part}`} aria-hidden="true" />
                {BUDGET_LABEL[part] ?? part}: {tokens}
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Konfiguration</h3>
      <pre className="sys-pre">{JSON.stringify(data.config, null, 2)}</pre>
    </div>
  );
}
