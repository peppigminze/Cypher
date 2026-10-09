import { describe, expect, it } from "vitest";
import { bubbleState, initialVoiceState, shouldStreamAudio, voiceReducer, type VoiceAction, type VoiceUiState } from "../voiceReducer";
import { caption } from "../voiceText";
import { plainText } from "../../utils/plainText";

function run(actions: VoiceAction[], start: VoiceUiState = initialVoiceState): VoiceUiState {
  return actions.reduce(voiceReducer, start);
}

const server = (message: Record<string, unknown>): VoiceAction => ({ type: "server", message: message as { type: string }, now: 1000 });
const open: VoiceAction[] = [
  { type: "connection", status: "open" },
  server({ type: "ready", mode: "wake", wake_word_enabled: true, wake_phrase: "Hey Cypher" }),
];

describe("voiceReducer", () => {
  it("follows a full spoken turn with live subtitles", () => {
    const s = run([
      ...open,
      server({ type: "state", state: "listening", turn_id: 1 }),
      server({ type: "vad", speech: true }),
      server({ type: "transcript", text: "Wie spät ist es?" }),
      server({ type: "state", state: "thinking", turn_id: 2 }),
      server({ type: "reply_delta", text: "Es ist " }),
      server({ type: "reply_delta", text: "zwölf Uhr." }),
      server({ type: "state", state: "speaking", turn_id: 2 }),
      server({ type: "audio_end", turn_id: 2, metrics: { first_audible: 1.4 } }),
    ]);
    expect(s.mode).toBe("wake");
    expect(s.transcript).toBe("Wie spät ist es?");
    expect(s.reply).toBe("Es ist zwölf Uhr.");
    expect(s.userSpeaking).toBe(false);
    expect(s.lastMetrics).toEqual({ first_audible: 1.4 });
    expect(bubbleState(s)).toBe("speaking");
  });

  it("shows an approval card until it is resolved", () => {
    const s1 = run([
      ...open,
      server({ type: "state", state: "thinking" }),
      server({
        type: "approval",
        card: { approval_id: "a1", tool_name: "save_note", summary: "Notiz speichern: „Brot“", risk: "write", expires_in_seconds: 300 },
      }),
      server({ type: "state", state: "speaking" }), // the question is read aloud
      server({ type: "state", state: "confirming" }),
    ]);
    expect(s1.approval).toMatchObject({ approvalId: "a1", risk: "write", expiresAt: 1000 + 300000 });
    expect(caption(s1)).toContain("ja");
    const s2 = run([server({ type: "approval_resolved", approval_id: "a1", approved: true })], s1);
    expect(s2.approval).toBeNull();
  });

  it("a new listening phase clears the old subtitles", () => {
    const s = run([
      ...open,
      server({ type: "transcript", text: "alt" }),
      server({ type: "reply", text: "alt" }),
      server({ type: "state", state: "listening" }),
    ]);
    expect(s.transcript).toBe("");
    expect(s.reply).toBe("");
  });

  it("a dropped connection resets the conversation state", () => {
    const s = run([...open, server({ type: "state", state: "speaking" }), { type: "connection", status: "closed" }]);
    expect(s.serverState).toBe("idle");
    expect(bubbleState(s)).toBe("offline");
    expect(caption(s)).toContain("getrennt");
  });

  it("ignores text that is still in flight after an interrupt", () => {
    const s = run([
      ...open,
      server({ type: "state", state: "speaking", turn_id: 4 }),
      server({ type: "reply_delta", text: "Erster Satz. ", turn_id: 4 }),
      { type: "local_interrupt" },
      server({ type: "reply_delta", text: "Zweiter Satz.", turn_id: 4 }),
    ]);
    expect(s.reply).toBe("Erster Satz. ");
    expect(s.serverState).toBe("listening");
    const next = run(
      [server({ type: "transcript", text: "Neu", turn_id: 6 }), server({ type: "reply_delta", text: "Okay.", turn_id: 6 })],
      s
    );
    expect(next.reply).toBe("Okay.");
  });

  it("keeps German error messages and hints", () => {
    const s = run([
      ...open,
      server({
        type: "error",
        code: "ollama_unreachable",
        message: "Ich erreiche mein Sprachmodell gerade nicht.",
        hint: "Ollama starten.",
      }),
    ]);
    expect(s.error).toEqual({
      code: "ollama_unreachable",
      message: "Ich erreiche mein Sprachmodell gerade nicht.",
      hint: "Ollama starten.",
    });
  });
});

describe("shouldStreamAudio (privacy: nothing leaves the browser unless needed)", () => {
  const base = run(open);
  it("never without mic or connection", () => {
    expect(shouldStreamAudio(base, false)).toBe(false);
  });
  it("always in wake mode (listens for the wake word)", () => {
    expect(shouldStreamAudio({ ...base, micEnabled: true, mode: "wake", serverState: "speaking" }, false)).toBe(true);
  });
  it("in tap mode only while the server listens", () => {
    const s = { ...base, micEnabled: true, mode: "auto" as const };
    expect(shouldStreamAudio({ ...s, serverState: "idle" }, false)).toBe(false);
    expect(shouldStreamAudio({ ...s, serverState: "listening" }, false)).toBe(true);
    expect(shouldStreamAudio({ ...s, serverState: "confirming" }, false)).toBe(true);
  });
  it("in push-to-talk only while held", () => {
    const s = { ...base, micEnabled: true, mode: "ptt" as const, serverState: "listening" as const };
    expect(shouldStreamAudio(s, false)).toBe(false);
    expect(shouldStreamAudio(s, true)).toBe(true);
  });
});

describe("plainText (subtitles)", () => {
  it("drops Markdown markers", () => {
    expect(plainText("**1. Antrieb:** Ein * Motor * **Vorteil:** schnell ## Ende")).toBe("1. Antrieb: Ein Motor Vorteil: schnell Ende");
    expect(plainText("- Milch\n- Brot: ja - klar")).toBe("Milch\nBrot: ja - klar");
  });
});

describe("voiceReducer - follow-up window", () => {
  const answered = [
    ...open,
    server({ type: "state", state: "thinking", turn_id: 2 }),
    server({ type: "transcript", text: "Wie spät ist es?" }),
    server({ type: "reply", text: "Zwölf Uhr." }),
    server({ type: "state", state: "speaking", turn_id: 2 }),
  ];

  it("opens with the server's deadline and keeps the answer readable", () => {
    const s = run([...answered, server({ type: "state", state: "listening", turn_id: 2, followup_seconds: 10, followup_total: 10 })]);
    expect(s.followup).toEqual({ phase: "open", until: 1000 + 10_000, total: 10_000 });
    expect(s.reply).toBe("Zwölf Uhr.");
    expect(bubbleState(s)).toBe("followup");
    expect(caption(s)).toBe("Ich höre weiter zu - frag einfach weiter.");
  });

  it("pauses when you start talking and closes when the server moves on", () => {
    const opened = run([...answered, server({ type: "state", state: "listening", followup_seconds: 10, followup_total: 10 })]);
    const talking = run([server({ type: "vad", speech: true }), server({ type: "followup", active: false, reason: "speech" })], opened);
    expect(talking.followup.phase).toBe("paused");
    expect(bubbleState(talking)).toBe("listening");
    const thinking = run([server({ type: "state", state: "thinking", turn_id: 3 })], talking);
    expect(thinking.followup.phase).toBe("off");
    const asleep = run([server({ type: "state", state: "wake_listening" })], opened);
    expect(asleep.followup.phase).toBe("off");
  });

  it("reopens with the time that is left after noise", () => {
    const opened = run([...answered, server({ type: "state", state: "listening", followup_seconds: 10, followup_total: 10 })]);
    const s = run(
      [
        server({ type: "followup", active: false }),
        server({ type: "state", state: "thinking" }),
        server({ type: "state", state: "listening", followup_seconds: 4, followup_total: 10 }),
      ],
      opened
    );
    expect(s.followup).toEqual({ phase: "open", until: 1000 + 4_000, total: 10_000 });
  });

  it("a dropped connection ends the window", () => {
    const opened = run([...answered, server({ type: "state", state: "listening", followup_seconds: 10, followup_total: 10 })]);
    expect(run([{ type: "connection", status: "closed" }], opened).followup.phase).toBe("off");
  });

  it("a normal listening state (after the wake word) is not a follow-up window", () => {
    const s = run([...open, server({ type: "state", state: "listening", turn_id: 1 })]);
    expect(s.followup.phase).toBe("off");
    expect(bubbleState(s)).toBe("listening");
  });
});
