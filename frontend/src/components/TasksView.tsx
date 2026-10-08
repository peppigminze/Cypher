import { useCallback, useState } from "react";
import type { Recurrence, Task, TaskPriority } from "../types";
import { completeTask, createTask, deleteTask, listTasks } from "../services/api";
import { usePolling } from "../hooks/usePolling";

const RECURRENCE_LABEL: Record<Recurrence, string> = {
  none: "einmalig",
  daily: "täglich",
  weekly: "wöchentlich",
  monthly: "monatlich",
};

export function TasksView() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("medium");
  const [dueAt, setDueAt] = useState("");
  const [reminderEnabled, setReminderEnabled] = useState(false);
  const [recurrence, setRecurrence] = useState<Recurrence>("none");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setTasks(await listTasks());
    } catch {
      // keep showing last known list
    }
  }, []);

  usePolling(refresh, 6000, []);

  async function handleCreate() {
    const t = title.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      const task = await createTask({
        title: t,
        priority,
        due_at: dueAt ? new Date(dueAt).toISOString() : undefined,
        reminder_enabled: reminderEnabled && !!dueAt,
        recurrence: dueAt ? recurrence : "none",
      });
      setTasks((prev) => [task, ...prev]);
      setTitle("");
      setDueAt("");
      setReminderEnabled(false);
      setRecurrence("none");
    } finally {
      setBusy(false);
    }
  }

  async function handleComplete(id: number) {
    const updated = await completeTask(id);
    setTasks((prev) => prev.map((t) => (t.id === id ? updated : t)));
  }

  async function handleDelete(id: number) {
    await deleteTask(id);
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }

  return (
    <div>
      <div className="list-header">
        <h1>Tasks</h1>
        <div className="inline-form">
          <input
            className="text-input"
            placeholder="Neue Aufgabe..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <select className="select-input" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
            <option value="low">Niedrig</option>
            <option value="medium">Mittel</option>
            <option value="high">Hoch</option>
          </select>
          <input
            className="text-input"
            type="datetime-local"
            value={dueAt}
            onChange={(e) => setDueAt(e.target.value)}
            title="Fälligkeit / Erinnerung"
          />
          {dueAt && (
            <>
              <label className="reminder-toggle">
                <input type="checkbox" checked={reminderEnabled} onChange={(e) => setReminderEnabled(e.target.checked)} />
                🔔 Erinnern
              </label>
              {reminderEnabled && (
                <select className="select-input" value={recurrence} onChange={(e) => setRecurrence(e.target.value as Recurrence)}>
                  <option value="none">einmalig</option>
                  <option value="daily">täglich</option>
                  <option value="weekly">wöchentlich</option>
                  <option value="monthly">monatlich</option>
                </select>
              )}
            </>
          )}
          <button className="btn-primary" onClick={handleCreate} disabled={busy || !title.trim()}>
            Hinzufügen
          </button>
        </div>
      </div>

      {tasks.length === 0 ? (
        <div className="empty-state">Noch keine Aufgaben. Leg oben deine erste an.</div>
      ) : (
        <div className="card-list">
          {tasks.map((t) => (
            <div className="card" key={t.id}>
              <span className={`priority-dot priority-${t.priority}`} />
              <div className="card__body">
                <p className={`card__title ${t.status === "completed" ? "is-done" : ""}`}>
                  {t.reminder_enabled && "🔔 "}
                  {t.title}
                </p>
                <div className="card__meta">
                  {t.due_at && (
                    <span>
                      Fällig: {new Date(t.due_at).toLocaleString("de-CH")}
                      {t.recurrence !== "none" && ` (${RECURRENCE_LABEL[t.recurrence]})`}
                    </span>
                  )}
                  <span>Erstellt: {new Date(t.created_at).toLocaleDateString("de-CH")}</span>
                  {t.tags.map((tag) => (
                    <span className="category-tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
              <div className="card__actions">
                {t.status !== "completed" && (
                  <button className="icon-btn" onClick={() => handleComplete(t.id)}>
                    Erledigt
                  </button>
                )}
                <button className="icon-btn" onClick={() => handleDelete(t.id)}>
                  Löschen
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
