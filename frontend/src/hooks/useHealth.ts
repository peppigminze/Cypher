import { useEffect, useState } from "react";
import { getHealth, type ComponentHealth, type HealthReport } from "../services/api";

export interface HealthProblem {
  component: string;
  severity: "error" | "degraded";
  message: string;
  hint: string;
}

export interface HealthState {
  /** null until the first answer */
  report: HealthReport | null;
  reachable: boolean;
  problems: HealthProblem[];
}

// The order says what matters most for a voice assistant.
const ORDER = ["ollama", "llm_model", "database", "gpu", "stt", "tts"];

export function problemsOf(report: HealthReport | null): HealthProblem[] {
  if (!report) return [];
  return Object.entries(report.components)
    .filter(([, c]: [string, ComponentHealth]) => c.status !== "ok")
    .map(([component, c]) => ({
      component,
      severity: c.status === "error" ? ("error" as const) : ("degraded" as const),
      message: c.message,
      hint: c.hint,
    }))
    .sort((a, b) =>
      a.severity === b.severity ? ORDER.indexOf(a.component) - ORDER.indexOf(b.component) : a.severity === "error" ? -1 : 1
    );
}

/** Polls the public /api/health (every 10 s, every 3 s while something is
 * wrong) - the source for the designed error states ("Ollama läuft nicht",
 * "Backend nicht erreichbar"), which also clear by themselves once fixed. */
export function useHealth(intervalMs = 10000): HealthState {
  const [state, setState] = useState<HealthState>({ report: null, reachable: true, problems: [] });

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();
    const poll = async () => {
      let next: HealthState;
      try {
        const report = await getHealth(controller.signal);
        next = { report, reachable: true, problems: problemsOf(report) };
      } catch {
        if (!alive) return;
        next = { report: null, reachable: false, problems: [] };
      }
      if (!alive) return;
      setState(next);
      const healthy = next.reachable && next.problems.length === 0;
      timer = setTimeout(poll, healthy ? intervalMs : 3000);
    };
    void poll();
    return () => {
      alive = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs]);

  return state;
}
