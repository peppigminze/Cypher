import { describe, expect, it } from "vitest";
import {
  FOLLOWUP_FLOW,
  INPUTS,
  SERVER_FLOW,
  commandFor,
  followupLeft,
  followupNext,
  isExpectedTransition,
  micStreams,
  type FollowupEvent,
  type FollowupPhase,
  type UiInput,
} from "../voiceMachine";
import type { ServerVoiceState } from "../voiceReducer";

const STATES: ServerVoiceState[] = ["idle", "wake_listening", "listening", "thinking", "speaking", "confirming"];
const INPUT_NAMES: UiInput[] = ["TAP", "ESCAPE", "YES", "NO"];

describe("voice state machine", () => {
  it("answers every input in every state (no dead buttons, no undefined behaviour)", () => {
    for (const state of STATES) {
      for (const input of INPUT_NAMES) expect(INPUTS[state][input]).toBeDefined();
      // The bubble always does something - except while a decision is pending.
      if (state !== "confirming") expect(commandFor(state, "TAP")).not.toBe("none");
    }
  });

  it("maps the bubble to the expected command per state", () => {
    expect(commandFor("idle", "TAP")).toBe("start");
    expect(commandFor("wake_listening", "TAP")).toBe("start");
    expect(commandFor("listening", "TAP")).toBe("stop");
    expect(commandFor("thinking", "TAP")).toBe("interrupt");
    expect(commandFor("speaking", "ESCAPE")).toBe("interrupt");
  });

  it("an accidental tap can't decide an approval; J/N and Escape can", () => {
    expect(commandFor("confirming", "TAP")).toBe("none");
    expect(commandFor("confirming", "YES")).toBe("approve");
    expect(commandFor("confirming", "NO")).toBe("reject");
    expect(commandFor("confirming", "ESCAPE")).toBe("reject");
    expect(commandFor("idle", "YES")).toBe("none");
  });

  it("knows the server's normal flow", () => {
    for (const state of STATES) expect(SERVER_FLOW[state].length).toBeGreaterThan(0);
    expect(isExpectedTransition("listening", "thinking")).toBe(true);
    expect(isExpectedTransition("thinking", "speaking")).toBe(true);
    expect(isExpectedTransition("idle", "speaking")).toBe(false);
    expect(isExpectedTransition("speaking", "speaking")).toBe(true);
  });

  it("streams the microphone only when the server needs it", () => {
    expect(micStreams("idle", "auto", false)).toBe(false);
    expect(micStreams("listening", "auto", false)).toBe(true);
    expect(micStreams("speaking", "wake", false)).toBe(true); // barge-in by "Hey Cypher"
    expect(micStreams("listening", "ptt", false)).toBe(false);
    expect(micStreams("listening", "ptt", true)).toBe(true);
    expect(micStreams("confirming", "auto", false)).toBe(true); // spoken ja/nein
  });
});

describe("follow-up window (sub-state of listening)", () => {
  const PHASES: FollowupPhase[] = ["off", "open", "paused"];
  const EVENTS: FollowupEvent[] = ["open", "speech", "close"];

  it("every phase answers every event", () => {
    for (const phase of PHASES) for (const event of EVENTS) expect(PHASES).toContain(FOLLOWUP_FLOW[phase][event]);
  });

  it("opens after an answer, pauses while you talk, reopens after noise and closes on anything else", () => {
    expect(followupNext("off", "open")).toBe("open");
    expect(followupNext("open", "speech")).toBe("paused");
    expect(followupNext("paused", "open")).toBe("open");
    for (const phase of PHASES) expect(followupNext(phase, "close")).toBe("off");
    expect(followupNext("off", "speech")).toBe("off"); // talking without a window changes nothing
  });

  it("the server moves from speaking straight into the window", () => {
    expect(isExpectedTransition("speaking", "listening")).toBe(true);
    expect(isExpectedTransition("listening", "wake_listening")).toBe(true);
  });

  it("a tap inside the window ends it (listening: TAP = stop)", () => {
    expect(commandFor("listening", "TAP")).toBe("stop");
  });

  it("computes the share left for the ring", () => {
    expect(followupLeft(11_000, 10_000, 1_000)).toBe(1);
    expect(followupLeft(11_000, 10_000, 6_000)).toBe(0.5);
    expect(followupLeft(11_000, 10_000, 20_000)).toBe(0);
    expect(followupLeft(0, 0, 0)).toBe(0);
  });
});
