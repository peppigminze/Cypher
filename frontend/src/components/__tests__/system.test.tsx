import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HealthBanner } from "../HealthBanner";
import { problemsOf } from "../../hooks/useHealth";
import { SystemMenu } from "../system/SystemMenu";
import { AuditPanel } from "../system/AuditPanel";
import * as api from "../../services/api";

vi.mock("../../services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof api>();
  return { ...actual, getAudit: vi.fn(), getDiagnostics: vi.fn(), getLogs: vi.fn(), getMemoryOverview: vi.fn(() => new Promise(() => {})) };
});

const report = (components: Record<string, { status: string; message: string; hint: string }>): api.HealthReport => ({
  status: "error",
  version: "t",
  uptime_seconds: 1,
  components,
});

describe("health → designed error states", () => {
  it("orders problems: errors first, Ollama before the rest", () => {
    const problems = problemsOf(
      report({
        tts: { status: "degraded", message: "Stimme fehlt.", hint: "" },
        gpu: { status: "error", message: "GPU weg.", hint: "" },
        ollama: { status: "error", message: "Ollama läuft nicht.", hint: "Starten." },
        database: { status: "ok", message: "ok", hint: "" },
      })
    );
    expect(problems.map((p) => p.component)).toEqual(["ollama", "gpu", "tts"]);
  });

  it("shows nothing when all is well", () => {
    const { container } = render(<HealthBanner health={{ report: null, reachable: true, problems: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says what is broken, what it means and what to do", () => {
    render(
      <HealthBanner
        health={{
          report: null,
          reachable: true,
          problems: [
            { component: "ollama", severity: "error", message: "Ollama läuft nicht.", hint: "Starte Ollama." },
            { component: "tts", severity: "degraded", message: "Stimme fehlt.", hint: "" },
          ],
        }}
      />
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Sprachmodell-Server");
    expect(alert).toHaveTextContent("Ollama läuft nicht. Starte Ollama.");
    expect(alert).toHaveTextContent("+1 weitere Hinweise");
  });

  it("explains an unreachable backend", () => {
    render(<HealthBanner health={{ report: null, reachable: false, problems: [] }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Cypher-Backend nicht erreichbar");
  });
});

describe("SystemMenu", () => {
  afterEach(() => vi.clearAllMocks());

  it("is a modal dialog: Escape closes, sections switch", async () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(<SystemMenu section="memory" onSelect={onSelect} onClose={onClose} />);
    expect(screen.getByRole("dialog", { name: "Gedächtnis" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Gedächtnis/ })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("button", { name: /Audit-Log/ }));
    expect(onSelect).toHaveBeenCalledWith("audit");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("puts focus inside the dialog", () => {
    render(<SystemMenu section="memory" onSelect={() => {}} onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "Menü schliessen (Esc)" })).toHaveFocus();
  });
});

describe("AuditPanel", () => {
  it("filters for refused actions", async () => {
    vi.mocked(api.getAudit).mockResolvedValue([
      {
        id: 1,
        ts: "2026-10-08T10:00:00Z",
        conversation_id: null,
        source: "voice",
        provider: "local",
        tool: "set_volume",
        risk: "control",
        decision: "auto",
        summary: "Leiser",
        arguments: {},
        success: true,
        error: null,
        duration_ms: 3,
      },
      {
        id: 2,
        ts: "2026-10-08T10:01:00Z",
        conversation_id: null,
        source: "text",
        provider: "local",
        tool: "remember",
        risk: "write",
        decision: "tainted_blocked",
        summary: "Merken: „Passwort“",
        arguments: {},
        success: false,
        error: "untrusted",
        duration_ms: null,
      },
    ]);
    render(<AuditPanel />);
    expect(await screen.findByText("Leiser")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Verweigert" }));
    expect(screen.queryByText("Leiser")).not.toBeInTheDocument();
    expect(screen.getByText("verweigert (fremder Inhalt)")).toBeInTheDocument();
  });
});
