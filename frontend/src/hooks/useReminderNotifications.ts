import { useRef } from "react";
import { getDueReminders } from "../services/api";
import { ensureNotificationPermission } from "../services/notifications";
import { usePolling } from "./usePolling";

/**
 * Polls /api/tasks/due-reminders and shows a browser Notification for
 * any reminder Cypher hasn't already surfaced this session (project
 * spec section 31 - push notifications are prepared as a native/browser
 * Notification here; a proper mobile push channel is a later phase).
 * Silently does nothing if the user hasn't granted notification
 * permission - it never nags for it more than once per session.
 */
export function useReminderNotifications(intervalMs = 15000) {
  // Keyed by `${task.id}-${last_notified_at}` rather than just task.id,
  // so a *recurring* reminder (whose last_notified_at moves forward
  // each time it re-fires) notifies again on its next occurrence
  // instead of being silenced for the rest of the session.
  const seenRef = useRef<Set<string>>(new Set());

  usePolling(async () => {
    if (!(await ensureNotificationPermission())) return;

    let due;
    try {
      due = await getDueReminders();
    } catch {
      return;
    }

    for (const task of due) {
      const key = `${task.id}-${task.last_notified_at}`;
      if (seenRef.current.has(key)) continue;
      seenRef.current.add(key);
      new Notification("Cypher Erinnerung", { body: task.title, tag: `jarvis-reminder-${task.id}` });
    }
  }, intervalMs);
}
