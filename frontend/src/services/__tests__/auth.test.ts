import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearToken, consumePairingHash, ensureToken, getStoredToken, pairWithCode, storeToken } from "../auth";

function respond(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("token handling (no token in the bundle)", () => {
  beforeEach(() => {
    localStorage.clear();
    window.location.hash = "";
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stores a token from the pairing link and removes it from the URL", () => {
    window.location.hash = "#pair=abcdefghijklmnop1234";
    expect(consumePairingHash()).toBe(true);
    expect(getStoredToken()).toBe("abcdefghijklmnop1234");
    expect(window.location.hash).toBe("");
  });

  it("ignores malformed pairing hashes", () => {
    window.location.hash = "#pair=<script>";
    expect(consumePairingHash()).toBe(false);
    expect(getStoredToken()).toBeNull();
  });

  it("uses a stored valid token", async () => {
    storeToken("stored-token-123456789");
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(respond(200, { ok: true }));
    expect(await ensureToken()).toBe("ready");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("replaces an invalid stored token with the local session token", async () => {
    storeToken("old-token-123456789012");
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(respond(401))
      .mockResolvedValueOnce(respond(200, { token: "fresh-token-1234567890" }));
    expect(await ensureToken()).toBe("ready");
    expect(getStoredToken()).toBe("fresh-token-1234567890");
  });

  it("asks for pairing when the session endpoint refuses (other device)", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(respond(403, { detail: "Nur direkt am PC" }));
    expect(await ensureToken()).toBe("needs_pairing");
  });

  it("reports offline instead of discarding a token when the backend is down", async () => {
    storeToken("stored-token-123456789");
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await ensureToken()).toBe("offline");
    expect(getStoredToken()).toBe("stored-token-123456789");
  });

  it("pairs with a typed code only if the backend accepts it", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(respond(401)).mockResolvedValueOnce(respond(200, { ok: true }));
    expect(await pairWithCode("wrong")).toBe(false);
    expect(getStoredToken()).toBeNull();
    expect(await pairWithCode("  right-code-12345678  ")).toBe(true);
    expect(getStoredToken()).toBe("right-code-12345678");
    clearToken();
  });
});
