import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceClient, voiceSocketUrl, type ConnectionStatus } from "../voiceClient";
import { AUTH_REQUIRED_EVENT } from "../auth";

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 0;
  binaryType = "blob";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  // test helpers
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  receive(data: unknown) {
    this.onmessage?.({ data });
  }
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

function makeClient(overrides: Partial<ConstructorParameters<typeof VoiceClient>[0]> = {}) {
  const statuses: ConnectionStatus[] = [];
  const messages: unknown[] = [];
  const audio: Array<[unknown, ArrayBuffer]> = [];
  const client = new VoiceClient({
    url: "ws://test/api/voice/ws",
    getToken: () => "secret-token",
    onStatus: (s) => statuses.push(s),
    onMessage: (m) => messages.push(m),
    onAudio: (h, pcm) => audio.push([h, pcm]),
    WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
    backoffMs: [100, 200, 400],
    ...overrides,
  });
  return { client, statuses, messages, audio };
}

beforeEach(() => {
  FakeSocket.instances = [];
  localStorage.removeItem("cypher.conversation.v1");
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("VoiceClient", () => {
  it("authenticates with the first message, never in the URL", () => {
    const { client, statuses } = makeClient();
    client.connect();
    const ws = FakeSocket.instances[0];
    expect(ws.url).not.toContain("secret");
    ws.open();
    expect(JSON.parse(ws.sent[0] as string)).toEqual({ type: "auth", token: "secret-token", conversation_id: null });
    ws.receive(JSON.stringify({ type: "ready", mode: "auto", conversation_id: "abc123def4567890" }));
    expect(statuses).toEqual(["connecting", "open"]);
  });

  it("resumes the last conversation after a reconnect", () => {
    localStorage.removeItem("cypher.conversation.v1");
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].receive(JSON.stringify({ type: "ready", conversation_id: "abc123def4567890" }));
    FakeSocket.instances[0].drop();
    vi.advanceTimersByTime(100);
    FakeSocket.instances[1].open();
    expect(JSON.parse(FakeSocket.instances[1].sent[0] as string).conversation_id).toBe("abc123def4567890");
    FakeSocket.instances[1].receive(JSON.stringify({ type: "conversation", conversation_id: "ffff0000ffff0000" }));
    expect(localStorage.getItem("cypher.conversation.v1")).toBe("ffff0000ffff0000");
    client.close();
  });

  it("pairs an audio header with the following binary frame", () => {
    const { client, audio, messages } = makeClient();
    client.connect();
    const ws = FakeSocket.instances[0];
    ws.open();
    const header = { type: "audio", turn_id: 2, seq: 0, sample_rate: 22050, bytes: 4, text: "Hallo." };
    ws.receive(JSON.stringify(header));
    const pcm = new ArrayBuffer(4);
    ws.receive(pcm);
    expect(audio).toEqual([[header, pcm]]);
    expect(messages).toEqual([]); // headers are not passed on as messages
  });

  it("reconnects with backoff after a dropped connection", () => {
    const { client, statuses } = makeClient();
    client.connect();
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].drop();
    expect(statuses[statuses.length - 1]).toBe("closed");
    vi.advanceTimersByTime(99);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);
    FakeSocket.instances[1].drop();
    vi.advanceTimersByTime(200);
    expect(FakeSocket.instances).toHaveLength(3);
  });

  it("does not retry with a rejected token and asks the app to re-pair", () => {
    const onAuth = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuth);
    const { client, statuses } = makeClient();
    client.connect();
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].drop(4401);
    vi.advanceTimersByTime(10000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(statuses[statuses.length - 1]).toBe("unauthorized");
    expect(onAuth).toHaveBeenCalled();
    window.removeEventListener(AUTH_REQUIRED_EVENT, onAuth);
  });

  it("close() stops reconnecting; audio is only sent while open", () => {
    const { client } = makeClient();
    client.connect();
    const ws = FakeSocket.instances[0];
    client.sendAudio(new ArrayBuffer(2));
    expect(ws.sent).toHaveLength(0);
    ws.open();
    client.sendAudio(new ArrayBuffer(2));
    expect(ws.sent).toHaveLength(2); // auth + audio
    client.close();
    vi.advanceTimersByTime(10000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("builds the socket URL from the API base or the page origin", () => {
    expect(voiceSocketUrl("http://localhost:8000")).toBe("ws://localhost:8000/api/voice/ws");
    expect(voiceSocketUrl("", { origin: "https://pc.tailnet.ts.net" } as Location)).toBe("wss://pc.tailnet.ts.net/api/voice/ws");
  });
});
