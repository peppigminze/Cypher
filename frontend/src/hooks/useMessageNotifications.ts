import { useRef } from "react";
import { listMessages } from "../services/api";
import { ensureNotificationPermission } from "../services/notifications";
import { usePolling } from "./usePolling";

/**
 * Notifies when a message finishes processing (project spec section 31:
 * "Cypher action completed/failed") - useful when the PWA is
 * backgrounded/minimized and the PC agent finishes a request the user
 * isn't actively watching the chat for. Independent poller (like
 * useReminderNotifications) so it works regardless of which view is
 * currently open, not just while ChatView is mounted.
 */
export function useMessageNotifications(intervalMs = 4000) {
  // Tracks (id, status) pairs already notified, so a message isn't
  // re-notified every poll while its status stays the same, but a
  // resent/reprocessed message (new id) still notifies.
  const seenRef = useRef<Set<string>>(new Set());
  const isFirstRunRef = useRef(true);

  usePolling(async () => {
    // Skip while the tab is actively visible - the user already sees
    // the answer land in the chat, a notification on top is just noise.
    if (document.visibilityState === "visible") return;
    if (!(await ensureNotificationPermission())) return;

    let messages;
    try {
      messages = await listMessages();
    } catch {
      return;
    }

    // On first load, mark everything already-completed as "seen" so
    // opening the app doesn't fire a burst of notifications for old
    // history - only messages that finish *after* this point notify.
    if (isFirstRunRef.current) {
      isFirstRunRef.current = false;
      for (const m of messages) {
        if (m.status === "completed" || m.status === "failed") {
          seenRef.current.add(`${m.id}-${m.status}`);
        }
      }
      return;
    }

    for (const m of messages) {
      if (m.status !== "completed" && m.status !== "failed") continue;
      const key = `${m.id}-${m.status}`;
      if (seenRef.current.has(key)) continue;
      seenRef.current.add(key);

      if (m.status === "completed") {
        new Notification("Cypher hat geantwortet", {
          body: m.response || m.content,
          tag: `jarvis-message-${m.id}`,
        });
      } else {
        new Notification("Cypher: Aktion fehlgeschlagen", {
          body: m.error || "Die Aktion konnte nicht ausgeführt werden.",
          tag: `jarvis-message-${m.id}`,
        });
      }
    }
  }, intervalMs);
}
