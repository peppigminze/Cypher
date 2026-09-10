import { useCallback, useState } from "react";
import type { PendingAction } from "../types";
import { confirmAction, listPendingActions, rejectAction } from "../services/api";
import { usePolling } from "../hooks/usePolling";

const TOOL_LABELS: Record<string, string> = {
  delete_task: "Aufgabe löschen",
  write_file: "Datei schreiben",
  move_file: "Datei verschieben",
  copy_file: "Datei kopieren",
  delete_file: "Datei/Ordner löschen",
  run_command: "Terminal-Befehl ausführen",
  open_application: "Programm öffnen",
  close_application: "Programm schließen",
  git_commit: "Git-Commit erstellen",
  git_push: "Git-Push ausführen",
  git_pull: "Git-Pull ausführen",
};

const MAX_ARG_PREVIEW = 80;

function previewValue(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > MAX_ARG_PREVIEW ? `${text.slice(0, MAX_ARG_PREVIEW)}…` : text;
}

function describeAction(action: PendingAction): string {
  const label = TOOL_LABELS[action.tool_name] ?? action.tool_name;
  const args = Object.entries(action.arguments)
    .map(([k, v]) => `${k}: ${previewValue(v)}`)
    .join(", ");
  return args ? `${label} (${args})` : label;
}

interface Props {
  onResolved?: () => void;
}

export function ConfirmBar({ onResolved }: Props) {
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setActions(await listPendingActions());
    } catch {
      // Backend unreachable - keep showing the last known list.
    }
  }, []);

  usePolling(refresh, 4000, []);

  async function handleConfirm(id: number) {
    setBusyId(id);
    try {
      await confirmAction(id);
      setActions((prev) => prev.filter((a) => a.id !== id));
      onResolved?.();
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(id: number) {
    setBusyId(id);
    try {
      await rejectAction(id);
      setActions((prev) => prev.filter((a) => a.id !== id));
      onResolved?.();
    } finally {
      setBusyId(null);
    }
  }

  if (actions.length === 0) return null;

  return (
    <div className="confirm-bar">
      {actions.map((action) => (
        <div className="confirm-bar__item" key={action.id}>
          <span className="confirm-bar__icon">⚠️</span>
          <span className="confirm-bar__text">
            Cypher möchte <strong>{describeAction(action)}</strong> ausführen. Bestätigen?
          </span>
          <div className="confirm-bar__actions">
            <button
              className="confirm-bar__btn is-confirm"
              disabled={busyId === action.id}
              onClick={() => handleConfirm(action.id)}
            >
              Bestätigen
            </button>
            <button
              className="confirm-bar__btn is-reject"
              disabled={busyId === action.id}
              onClick={() => handleReject(action.id)}
            >
              Abbrechen
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
