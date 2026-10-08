import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState, type ChatAction, type ChatState } from "../chatState";
import { parseSse } from "../../services/chatStream";

const ev = (event: Record<string, unknown>): ChatAction => ({ type: "event", event: event as { type: "reply" }, now: 1000 });

function run(actions: ChatAction[], start: ChatState = initialChatState): ChatState {
  return actions.reduce(chatReducer, start);
}

describe("chatReducer", () => {
  it("streams a reply into the assistant turn", () => {
    const s = run([
      { type: "send", text: "Hallo" },
      ev({ type: "conversation", conversation_id: "abc123abc123abc1" }),
      ev({ type: "reply_delta", text: "Hallo " }),
      ev({ type: "reply_delta", text: "Silvan." }),
      ev({ type: "reply", text: "Hallo Silvan." }),
      { type: "finished" },
    ]);
    expect(s.conversationId).toBe("abc123abc123abc1");
    expect(s.turns.map((t) => [t.role, t.text])).toEqual([
      ["user", "Hallo"],
      ["assistant", "Hallo Silvan."],
    ]);
    expect(s.busy).toBe(false);
    expect(s.turns[1].streaming).toBe(false);
  });

  it("shows tools, pauses for approval and continues in the same turn", () => {
    const paused = run([
      { type: "send", text: "Notiere Brot" },
      ev({ type: "tool", phase: "start", name: "get_weather", summary: "Wetter" }),
      ev({ type: "tool", phase: "end", name: "get_weather", summary: "Wetter", success: true }),
      ev({
        type: "approval",
        card: { approval_id: "a1", tool_name: "save_note", summary: "Notiz speichern: „Brot“", risk: "write", expires_in_seconds: 60 },
      }),
      { type: "finished" },
    ]);
    const last = paused.turns[1];
    expect(last.tools).toEqual([{ name: "get_weather", summary: "Wetter", running: false, success: true }]);
    expect(last.approval).toMatchObject({ approvalId: "a1", expiresAt: 61000 });
    const resumed = run([{ type: "resume" }, ev({ type: "reply", text: "Notiz gespeichert." }), { type: "finished" }], paused);
    expect(resumed.turns).toHaveLength(2);
    expect(resumed.turns[1].approval).toBeNull();
    expect(resumed.turns[1].text).toBe("Notiz gespeichert.");
  });

  it("keeps German errors and hints", () => {
    const s = run([
      { type: "send", text: "x" },
      ev({ type: "error", message: "Ich erreiche mein Sprachmodell gerade nicht.", hint: "Ollama starten." }),
    ]);
    expect(s.turns[1].error).toEqual({ message: "Ich erreiche mein Sprachmodell gerade nicht.", hint: "Ollama starten." });
  });

  it("loads history and resets", () => {
    const s = run([
      {
        type: "load",
        conversationId: "c1",
        history: [
          { role: "user", text: "A" },
          { role: "assistant", text: "B" },
        ],
      },
    ]);
    expect(s.turns.map((t) => t.text)).toEqual(["A", "B"]);
    expect(run([{ type: "reset" }], s).turns).toEqual([]);
  });
});

describe("parseSse", () => {
  it("splits events across chunk boundaries", async () => {
    const chunks = ['data: {"type":"reply_delta","text":"Hal', 'lo"}\n\ndata: {"type":"reply","text":"Hallo"}\n', "\n"];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(new TextEncoder().encode(c));
        controller.close();
      },
    });
    const out = [];
    for await (const e of parseSse(stream)) out.push(e);
    expect(out).toEqual([
      { type: "reply_delta", text: "Hallo" },
      { type: "reply", text: "Hallo" },
    ]);
  });
});
