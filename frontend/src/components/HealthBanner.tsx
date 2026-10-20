import type { HealthState } from "../hooks/useHealth";

const LABEL: Record<string, string> = {
  ollama: "Sprachmodell-Server",
  llm_model: "Sprachmodell",
  database: "Datenbank",
  gpu: "Grafikkarte",
  stt: "Spracherkennung",
  tts: "Sprachausgabe",
};

/**
 * Designed error states: when something Cypher needs is down, say what,
 * what it means, and what to do - and disappear on its own once fixed
 * (useHealth keeps polling). Errors first, then degraded parts.
 */
export function HealthBanner({ health }: { health: HealthState }) {
  if (!health.reachable) {
    return (
      <div className="health-banner health-banner--error" role="alert">
        <span className="health-banner__title">Cypher-Backend nicht erreichbar</span>
        <span className="health-banner__text">
          Ohne Backend kann ich weder hören noch antworten. Starte Cypher mit <code>start_cypher.bat</code> - ich verbinde mich danach von
          selbst.
        </span>
      </div>
    );
  }
  const problem = health.problems[0];
  if (!problem) return null;
  const more = health.problems.length - 1;
  return (
    <div className={`health-banner health-banner--${problem.severity}`} role={problem.severity === "error" ? "alert" : "status"}>
      <span className="health-banner__title">{LABEL[problem.component] ?? problem.component}</span>
      <span className="health-banner__text">
        {problem.message}
        {problem.hint && <span className="health-banner__hint"> {problem.hint}</span>}
        {more > 0 && <span className="health-banner__more"> (+{more} weitere Hinweise unter Diagnose)</span>}
      </span>
    </div>
  );
}
