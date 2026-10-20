import { afterEach, describe, expect, it, vi } from "vitest";

const g = globalThis as unknown as { Notification?: unknown };

describe("ensureNotificationPermission", () => {
  const originalNotification = g.Notification;

  afterEach(() => {
    g.Notification = originalNotification;
    vi.resetModules();
  });

  it("returns true immediately when already granted", async () => {
    g.Notification = { permission: "granted" };
    const { ensureNotificationPermission } = await import("../notifications");
    expect(await ensureNotificationPermission()).toBe(true);
  });

  it("returns false when denied, without prompting again", async () => {
    const requestPermission = vi.fn();
    g.Notification = { permission: "denied", requestPermission };
    const { ensureNotificationPermission } = await import("../notifications");
    expect(await ensureNotificationPermission()).toBe(false);
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("prompts once when permission is default, then reuses the granted permission", async () => {
    // Real browsers update Notification.permission once the user
    // answers the prompt - simulate that here.
    const fakeNotification: { permission: string; requestPermission: () => Promise<string> } = {
      permission: "default",
      requestPermission: vi.fn(async () => {
        fakeNotification.permission = "granted";
        return "granted";
      }),
    };
    g.Notification = fakeNotification;
    const { ensureNotificationPermission } = await import("../notifications");

    const first = await ensureNotificationPermission();
    const second = await ensureNotificationPermission();

    expect(first).toBe(true);
    expect(second).toBe(true);
    expect(fakeNotification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("does not re-prompt on a later call even if the user never answered (still default)", async () => {
    const requestPermission = vi.fn().mockResolvedValue("default");
    g.Notification = { permission: "default", requestPermission };
    const { ensureNotificationPermission } = await import("../notifications");

    const first = await ensureNotificationPermission();
    const second = await ensureNotificationPermission();

    expect(first).toBe(false);
    expect(second).toBe(false);
    expect(requestPermission).toHaveBeenCalledTimes(1);
  });

  it("returns false when the Notification API doesn't exist", async () => {
    delete g.Notification;
    const { ensureNotificationPermission } = await import("../notifications");
    expect(await ensureNotificationPermission()).toBe(false);
  });
});
