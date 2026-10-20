import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SafetyPromptSection } from "../SafetyPromptSection";
import { ApprovalCard } from "../ApprovalCard";
import { HudBar } from "../HudBar";
import * as api from "../../services/api";
import { initialVoiceState } from "../../hooks/voiceReducer";

vi.mock("../../services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../services/api")>();
  return { ...actual, getCriticalConfirm: vi.fn(), setCriticalConfirm: vi.fn() };
});

const ON: api.CriticalConfirmState = { enabled: true, mode: "on", red_line: ["format"], red_line_labels: ["Formatieren / Partitionieren"] };
const OFF_SESSION: api.CriticalConfirmState = { ...ON, enabled: false, mode: "off_session" };

describe("Sicherheitsabfrage (settings)", () => {
  beforeEach(() => {
    vi.mocked(api.getCriticalConfirm).mockResolvedValue(ON);
  });
  afterEach(() => vi.clearAllMocks());

  it("is ON by default and shows the red line", async () => {
    render(<SafetyPromptSection />);
    expect(await screen.findByRole("status")).toHaveTextContent("AN");
    expect(screen.getByText(/rote Linie/)).toHaveTextContent("Formatieren / Partitionieren");
  });

  it("turning it off needs the warning and a scope choice", async () => {
    vi.mocked(api.setCriticalConfirm).mockResolvedValue(OFF_SESSION);
    render(<SafetyPromptSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Ausschalten …" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Wirklich ohne Nachfrage?");
    expect(api.setCriticalConfirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Nur für diese Sitzung" }));
    expect(api.setCriticalConfirm).toHaveBeenCalledWith(false, "session");
  });

  it("cancel keeps it on", async () => {
    render(<SafetyPromptSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Ausschalten …" }));
    await userEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.setCriticalConfirm).not.toHaveBeenCalled();
  });

  it("when off: shows it and can switch back on", async () => {
    vi.mocked(api.getCriticalConfirm).mockResolvedValue(OFF_SESSION);
    vi.mocked(api.setCriticalConfirm).mockResolvedValue(ON);
    render(<SafetyPromptSection />);
    expect(await screen.findByRole("status")).toHaveTextContent("AUS bis zum nächsten Neustart");
    await userEvent.click(screen.getByRole("button", { name: "Wieder einschalten" }));
    expect(api.setCriticalConfirm).toHaveBeenCalledWith(true, "permanent");
  });

  it("shows the backend's refusal (e.g. on the phone)", async () => {
    vi.mocked(api.setCriticalConfirm).mockRejectedValue(new Error("Die Sicherheitsabfrage lässt sich nur direkt am PC ... ändern"));
    render(<SafetyPromptSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Ausschalten …" }));
    await userEvent.click(screen.getByRole("button", { name: "Dauerhaft ausschalten" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("nur direkt am PC"));
  });
});

describe("HUD", () => {
  it("always shows when the safety prompt is off", () => {
    const { rerender } = render(<HudBar state={initialVoiceState} provider="local" onOpenMenu={() => undefined} safety={ON} />);
    expect(screen.queryByText("⚠ ABFRAGE AUS")).toBeNull();
    rerender(<HudBar state={initialVoiceState} provider="local" onOpenMenu={() => undefined} safety={OFF_SESSION} />);
    expect(screen.getByText("⚠ ABFRAGE AUS")).toHaveAttribute("title", expect.stringContaining("bis zum Neustart"));
  });
});

describe("Approval card for critical actions", () => {
  const card = {
    approvalId: "a1",
    toolName: "run_command",
    summary: "PowerShell: Remove-Item -Recurse C:\\Users\\me\\Alt",
    risk: "critical",
    arguments: { command: "Remove-Item -Recurse C:\\Users\\me\\Alt" },
    expiresAt: Date.now() + 60_000,
    reason: "löscht endgültig (ohne Papierkorb)",
    kind: "critical",
    voicePhrase: "Ja, ausführen",
  };

  it("shows tool, exact command, risk, reason and the voice phrase", async () => {
    const decide = vi.fn();
    render(<ApprovalCard card={card} onDecide={decide} />);
    expect(screen.getByText("Kritische Aktion")).toBeInTheDocument();
    expect(screen.getByText("Grund: löscht endgültig (ohne Papierkorb)")).toBeInTheDocument();
    expect(screen.getByText("Werkzeug run_command - genaue Argumente")).toBeInTheDocument();
    expect(screen.getByText("Remove-Item -Recurse C:\\Users\\me\\Alt", { selector: "code" })).toBeInTheDocument();
    expect(screen.getByText(/Per Sprache: „Ja, ausführen“/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ausführen" }));
    expect(decide).toHaveBeenCalledWith(true);
  });

  it("red line and untrusted content are explained", () => {
    render(<ApprovalCard card={{ ...card, kind: "red_line" }} onDecide={() => undefined} />);
    expect(screen.getByText(/Rote Linie/)).toBeInTheDocument();
  });

  it("a critical card from untrusted content still asks for the phrase", () => {
    render(<ApprovalCard card={{ ...card, kind: "untrusted" }} onDecide={() => undefined} />);
    expect(screen.getByText(/stammt \(auch\) aus fremdem Inhalt/)).toBeInTheDocument();
    expect(screen.getByText(/Per Sprache: „Ja, ausführen“/)).toBeInTheDocument();
  });

  it("a normal write card keeps the plain yes", () => {
    render(<ApprovalCard card={{ ...card, risk: "write", kind: "write", voicePhrase: "Ja", reason: "" }} onDecide={() => undefined} />);
    expect(screen.getByRole("button", { name: "Ja" })).toBeInTheDocument();
    expect(screen.queryByText(/Per Sprache/)).toBeNull();
  });
});
