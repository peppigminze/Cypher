import type { Page, WebSocketRoute } from "@playwright/test";

export const TOKEN = "e2e-test-token-not-a-real-secret";

export const HEALTHY = {
  status: "ok",
  version: "e2e",
  uptime_seconds: 60,
  components: {
    database: { status: "ok", message: "Datenbank erreichbar.", hint: "" },
    ollama: { status: "ok", message: "Ollama erreichbar.", hint: "" },
    llm_model: { status: "ok", message: "qwen3.5:9b geladen.", hint: "" },
  },
};

export const OLLAMA_DOWN = {
  ...HEALTHY,
  status: "error",
  components: {
    ...HEALTHY.components,
    ollama: { status: "error", message: "Ollama läuft nicht.", hint: "Starte Ollama (start_cypher.bat startet es mit)." },
  },
};

/** A tiny fake of app/voice/session.py: answers typed text with a streamed reply. */
export function fakeVoiceServer(ws: WebSocketRoute) {
  let turn = 0;
  const send = (msg: object) => ws.send(JSON.stringify(msg));
  ws.onMessage((raw) => {
    if (typeof raw !== "string") return;
    const msg = JSON.parse(raw) as { type: string; text?: string; token?: string };
    if (msg.type === "auth") {
      send({
        type: "ready",
        session_id: "e2e",
        conversation_id: "e2e0e2e0e2e0e2e0",
        mode: "auto",
        wake_word_enabled: true,
        wake_phrase: "Hey Cypher",
      });
      send({ type: "state", state: "idle", turn_id: 0 });
    } else if (msg.type === "text") {
      turn += 1;
      send({ type: "transcript", text: msg.text, turn_id: turn });
      send({ type: "state", state: "thinking", turn_id: turn });
      send({ type: "reply_delta", text: "Es ist ", turn_id: turn });
      send({ type: "reply_delta", text: "zwölf Uhr.", turn_id: turn });
      send({ type: "reply", text: "Es ist zwölf Uhr.", turn_id: turn });
      send({ type: "audio_end", turn_id: turn, metrics: { first_audible: 0.8 } });
      send({ type: "state", state: "idle", turn_id: turn });
    } else if (msg.type === "ping") {
      send({ type: "pong" });
    }
  });
}

export async function mockBackend(page: Page, health: object = HEALTHY) {
  await page.route("**/api/health", (route) => route.fulfill({ json: health }));
  await page.route("**/api/auth/session", (route) => route.fulfill({ json: { token: TOKEN } }));
  await page.route("**/api/auth/check", (route) => route.fulfill({ json: { ok: true, role: "user" } }));
  await page.route("**/api/settings/provider", (route) =>
    route.fulfill({ json: { provider: "local", cloud_configured: false, cloud_status: "disabled" } })
  );
  await page.route("**/api/tasks**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/diagnostics", (route) =>
    route.fulfill({
      json: {
        ...health,
        component_details: {},
        latency: { e2e_first_audible: { count: 3, last_ms: 900, p50_ms: 1240, p90_ms: 1610 } },
        config: { llm_model: "qwen3.5:9b" },
        system: {},
        context_budget: {
          parts: { system_prompt: 324, tool_definitions: 2305, answer_reserve: 1024 },
          total: 3653,
          window: 8192,
          fits: true,
        },
      },
    })
  );
  await page.routeWebSocket(/\/api\/voice\/ws$/, fakeVoiceServer);
}
