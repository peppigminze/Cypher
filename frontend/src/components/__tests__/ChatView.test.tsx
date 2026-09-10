import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatView } from "../ChatView";
import * as api from "../../services/api";
import type { ChatMessage } from "../../types";

vi.mock("../../services/api");

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 1,
    client_id: null,
    content: "hallo",
    status: "completed",
    response: "hi zurück",
    error: null,
    retry_count: 0,
    processed_by: "local",
    observations: [],
    created_at: new Date().toISOString(),
    processed_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("ChatView", () => {
  beforeEach(() => {
    vi.mocked(api.flushOfflineQueue).mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no messages", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([]);
    render(<ChatView />);
    expect(await screen.findByText(/Schreib Cypher eine Nachricht/)).toBeInTheDocument();
  });

  it("renders an existing completed message and its response", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([makeMessage()]);
    render(<ChatView />);
    expect(await screen.findByText("hallo")).toBeInTheDocument();
    expect(await screen.findByText("hi zurück")).toBeInTheDocument();
  });

  it("shows a 🖥️ Lokal badge for a message answered locally", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([makeMessage({ processed_by: "local" })]);
    render(<ChatView />);
    expect(await screen.findByText("🖥️ Lokal")).toBeInTheDocument();
  });

  it("shows a ☁️ Cloud badge for a message answered via cloud fallback", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([makeMessage({ processed_by: "cloud" })]);
    render(<ChatView />);
    expect(await screen.findByText("☁️ Cloud")).toBeInTheDocument();
  });

  it("shows short tool-activity lines for a message that used tools", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([
      makeMessage({
        observations: [
          { tool: "create_task", arguments: { title: "Test" }, result: { id: 1 } },
          { tool: "run_command", arguments: { command: "git status" }, error: "timed out" },
        ],
      }),
    ]);
    render(<ChatView />);
    expect(await screen.findByText(/Aufgabe erstellt/)).toBeInTheDocument();
    expect(await screen.findByText(/Befehl ausgeführt/)).toBeInTheDocument();
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("✗")).toBeInTheDocument();
  });

  it("does not render a tool-activity block when no tools were used", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([makeMessage({ observations: [] })]);
    const { container } = render(<ChatView />);
    await screen.findByText("hallo");
    expect(container.querySelector(".tool-activity")).not.toBeInTheDocument();
  });

  it("shows a failed message's error text", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([
      makeMessage({ status: "failed", response: null, error: "Local LLM is unavailable." }),
    ]);
    render(<ChatView />);
    expect(await screen.findByText("Local LLM is unavailable.")).toBeInTheDocument();
    expect(screen.getByText("Fehlgeschlagen")).toBeInTheDocument();
  });

  it("sends a message and displays the result", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([]);
    const sent = makeMessage({ id: 2, content: "neue nachricht", status: "pending", response: null });
    vi.mocked(api.sendMessage).mockResolvedValue(sent);

    const user = userEvent.setup();
    render(<ChatView />);

    const input = await screen.findByPlaceholderText("Nachricht an Cypher...");
    await user.type(input, "neue nachricht");
    await user.click(screen.getByRole("button", { name: "Senden" }));

    expect(api.sendMessage).toHaveBeenCalledWith("neue nachricht");
    expect(await screen.findByText("neue nachricht")).toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("shows a queued message when offline (sendMessage falls back to the local queue)", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([]);
    vi.mocked(api.sendMessage).mockResolvedValue({ queued: true, client_id: "abc-123" });

    const user = userEvent.setup();
    render(<ChatView />);

    const input = await screen.findByPlaceholderText("Nachricht an Cypher...");
    await user.type(input, "offline nachricht");
    await user.click(screen.getByRole("button", { name: "Senden" }));

    await waitFor(() => expect(screen.getByText("offline nachricht")).toBeInTheDocument());
    expect(screen.getByText("Gespeichert (offline)")).toBeInTheDocument();
  });

  it("does not send an empty or whitespace-only message", async () => {
    vi.mocked(api.listMessages).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<ChatView />);

    const input = await screen.findByPlaceholderText("Nachricht an Cypher...");
    await user.type(input, "   ");
    const sendButton = screen.getByRole("button", { name: "Senden" });
    expect(sendButton).toBeDisabled();
    expect(api.sendMessage).not.toHaveBeenCalled();
  });
});
