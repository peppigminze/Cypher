import { useCallback, useEffect, useState } from "react";
import { CRITICAL_CONFIRM_EVENT, getCriticalConfirm, type CriticalConfirmState } from "../services/api";

/**
 * State of "Sicherheitsabfrage für kritische Aktionen" for the UI. Polls
 * every 30 s (another tab or the phone may show it) and updates at once
 * when this tab changes it (CRITICAL_CONFIRM_EVENT from setCriticalConfirm).
 * null = not known yet (or the backend is unreachable).
 */
export function useCriticalConfirm(): [CriticalConfirmState | null, () => void] {
  const [state, setState] = useState<CriticalConfirmState | null>(null);
  const refresh = useCallback(() => {
    getCriticalConfirm()
      .then(setState)
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    const onChange = (event: Event) => setState((event as CustomEvent<CriticalConfirmState>).detail);
    window.addEventListener(CRITICAL_CONFIRM_EVENT, onChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(CRITICAL_CONFIRM_EVENT, onChange);
    };
  }, [refresh]);
  return [state, refresh];
}
