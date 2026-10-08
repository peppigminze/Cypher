/**
 * WebSocket connection to the live voice session (backend:
 * app/api/voice_ws.py + app/voice/session.py - the wire protocol is
 * documented at the top of session.py).
 *
 *  - Auth: the token goes in the FIRST message, not in the URL (URLs end
 *    up in logs and browser history) and not in a header (browsers can't
 *    set headers on WebSockets).
 *  - Audio from the server arrives as a JSON header {"type":"audio", ...}
 *    immediately followed by one binary frame; they are paired here.
 *  - Reconnects with backoff (0.5 s ... 15 s) when the connection drops -
 *    e.g. the backend restarts or the phone switches networks - but NOT
 *    after "token invalid" (4401) or "origin not allowed" (4403): retrying
 *    can't fix those.
 *  - Heartbeat: a ping every 15 s; 40 s without any message counts as a
 *    dead connection (mobile networks drop silently).
 */
import { API_BASE_URL, AUTH_REQUIRED_EVENT, getStoredToken } from "./auth";

export type ConnectionStatus = "connecting" | "open" | "closed" | "unauthorized" | "forbidden";

export interface ServerMessage {
  type: string;
  [key: string]: unknown;
}

export interface AudioHeader extends ServerMessage {
  type: "audio";
  turn_id: number;
  seq: number;
  sample_rate: number;
  bytes: number;
  text: string;
}

export interface VoiceClientOptions {
  url?: string;
  getToken?: () => string | null;
  onMessage: (message: ServerMessage) => void;
  onAudio: (header: AudioHeader, pcm: ArrayBuffer) => void;
  onStatus: (status: ConnectionStatus) => void;
  WebSocketImpl?: typeof WebSocket;
  backoffMs?: number[];
}

const CONVERSATION_KEY = "cypher.conversation.v1";

/** The conversation this browser is in - sent on (re)connect so the
 * server can continue it (it decides whether it's recent enough). */
export function storedConversationId(): string | null {
  try {
    return localStorage.getItem(CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function rememberConversationId(id: unknown): void {
  if (typeof id !== "string") return;
  try {
    localStorage.setItem(CONVERSATION_KEY, id);
  } catch {
    /* private mode: just no resume */
  }
}

const CLOSE_UNAUTHORIZED = 4401;
const CLOSE_FORBIDDEN_ORIGIN = 4403;
const CLOSE_RATE_LIMIT = 4429;
const DEFAULT_BACKOFF_MS = [500, 1000, 2000, 4000, 8000, 15000];
const PING_INTERVAL_MS = 15000;
const DEAD_AFTER_MS = 40000;

export function voiceSocketUrl(base: string = API_BASE_URL, location: Location = window.location): string {
  const origin = base || location.origin;
  return origin.replace(/\/$/, "").replace(/^http/, "ws") + "/api/voice/ws";
}

export class VoiceClient {
  private ws: WebSocket | null = null;
  private pendingHeader: AudioHeader | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private closedByUser = false;
  private readonly opts: Required<Omit<VoiceClientOptions, "url">> & { url: string };

  constructor(options: VoiceClientOptions) {
    this.opts = {
      url: options.url ?? voiceSocketUrl(),
      getToken: options.getToken ?? getStoredToken,
      onMessage: options.onMessage,
      onAudio: options.onAudio,
      onStatus: options.onStatus,
      WebSocketImpl: options.WebSocketImpl ?? WebSocket,
      backoffMs: options.backoffMs ?? DEFAULT_BACKOFF_MS,
    };
  }

  connect(): void {
    this.closedByUser = false;
    this.clearReconnect();
    this.opts.onStatus("connecting");
    const ws = new this.opts.WebSocketImpl(this.opts.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "auth", token: this.opts.getToken() ?? "", conversation_id: storedConversationId() }));
      this.lastMessageAt = Date.now();
      this.startHeartbeat();
    };
    ws.onmessage = (event: MessageEvent) => this.handleMessage(event.data);
    ws.onclose = (event: CloseEvent) => this.handleClose(ws, event.code);
    ws.onerror = () => {
      /* onclose follows and handles it */
    };
  }

  close(): void {
    this.closedByUser = true;
    this.clearReconnect();
    this.stopHeartbeat();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.close(1000);
    }
    this.opts.onStatus("closed");
  }

  get isOpen(): boolean {
    return this.ws !== null && this.ws.readyState === this.opts.WebSocketImpl.OPEN;
  }

  send(message: Record<string, unknown>): boolean {
    if (!this.isOpen || !this.ws) return false;
    this.ws.send(JSON.stringify(message));
    return true;
  }

  sendAudio(pcm: ArrayBuffer): void {
    if (this.isOpen && this.ws) this.ws.send(pcm);
  }

  private handleMessage(data: unknown): void {
    this.lastMessageAt = Date.now();
    if (typeof data !== "string") {
      const header = this.pendingHeader;
      this.pendingHeader = null;
      if (header && data instanceof ArrayBuffer) this.opts.onAudio(header, data);
      return;
    }
    let message: ServerMessage;
    try {
      message = JSON.parse(data) as ServerMessage;
    } catch {
      return;
    }
    if (message.type === "audio") {
      this.pendingHeader = message as AudioHeader;
      return;
    }
    if (message.type === "ready") {
      this.attempt = 0;
      this.opts.onStatus("open");
    }
    if (message.type === "ready" || message.type === "conversation") rememberConversationId(message.conversation_id);
    this.opts.onMessage(message);
  }

  private handleClose(ws: WebSocket, code: number): void {
    if (ws !== this.ws) return;
    this.ws = null;
    this.pendingHeader = null;
    this.stopHeartbeat();
    if (code === CLOSE_UNAUTHORIZED) {
      this.opts.onStatus("unauthorized");
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
      return;
    }
    if (code === CLOSE_FORBIDDEN_ORIGIN) {
      this.opts.onStatus("forbidden");
      return;
    }
    this.opts.onStatus("closed");
    if (this.closedByUser) return;
    const delays = this.opts.backoffMs;
    let delay = delays[Math.min(this.attempt, delays.length - 1)];
    if (code === CLOSE_RATE_LIMIT) delay = Math.max(delay, delays[delays.length - 1]);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.pingTimer = setInterval(() => {
      if (Date.now() - this.lastMessageAt > DEAD_AFTER_MS) {
        // Silently dead (phone changed network): force a reconnect.
        const ws = this.ws;
        if (ws) this.handleClose(ws, 4000);
        ws?.close();
        return;
      }
      this.send({ type: "ping", t: Date.now() });
    }, PING_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.pingTimer !== null) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }
}
