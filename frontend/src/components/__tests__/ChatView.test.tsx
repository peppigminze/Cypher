import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatView } from "../ChatView";
import * as stream from "../../services/chatStream";

vi.mock("../../services/chatStream", async (importOriginal) => {
  const actual = await importOriginal<typeof stream>();
  return { ...actual, streamChat: vi.fn(), streamApproval: vi.fn(), fetchChatHistory: vi.fn(async () => []) };
});

async function* gen(events: stream.ChatEvent[]) {
  for (const e of events) yield e;
}

describe("ChatView (engine chat)", () => {
  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("sends a message and shows the streamed reply", async () => {
    vi.mocked(stream.streamChat).mockResolvedValue(
      gen([
        { type: "conversation", conversation_id: "abcdefabcdefabcd" },
        { type: "reply_delta", text: "Es ist " },
        { type: "reply", text: "Es ist zwölf Uhr." },
      ])
    );
    render(<ChatView />);
    await userEvent.type(screen.getByRole("textbox", { name: "Nachricht an Cypher" }), "Wie spät ist es?{Enter}");
    expect(await screen.findByText("Es ist zwölf Uhr.")).toBeInTheDocument();
    expect(screen.getByText("Wie spät ist es?")).toBeInTheDocument();
    expect(stream.streamChat).toHaveBeenCalledWith("Wie spät ist es?", null, expect.anything());
    expect(localStorage.getItem("cypher.chat.conversation.v1")).toBe("abcdefabcdefabcd");
  });

  it("answers an approval card in the chat", async () => {
    vi.mocked(stream.streamChat).mockResolvedValue(
      gen([{ type: "approval", card: { approval_id: "ap1", tool_name: "save_note", summary: "Notiz speichern: „Brot“", risk: "write", expires_in_seconds: 300 } }])
    );
    vi.mocked(stream.streamApproval).mockResolvedValue(gen([{ type: "reply", text: "Notiz gespeichert." }]));
    render(<ChatView />);
    await userEvent.type(screen.getByRole("textbox", { name: "Nachricht an Cypher" }), "Notiere Brot{Enter}");
    expect(await screen.findByText("Notiz speichern: „Brot“")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ja" }));
    expect(await screen.findByText("Notiz gespeichert.")).toBeInTheDocument();
    expect(stream.streamApproval).toHaveBeenCalledWith("ap1", true, expect.anything());
  });

  it("shows a German message when the backend is unreachable", async () => {
    vi.mocked(stream.streamChat).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<ChatView />);
    await userEvent.type(screen.getByRole("textbox", { name: "Nachricht an Cypher" }), "Hallo{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("nicht erreichbar");
  });

  it("restores the last conversation from the backend", async () => {
    localStorage.setItem("cypher.chat.conversation.v1", "abcdefabcdefabcd");
    vi.mocked(stream.fetchChatHistory).mockResolvedValue([
      { role: "user", text: "Merk dir Tee." },
      { role: "assistant", text: "Gemerkt." },
    ]);
    render(<ChatView />);
    expect(await screen.findByText("Gemerkt.")).toBeInTheDocument();
    expect(stream.fetchChatHistory).toHaveBeenCalledWith("abcdefabcdefabcd");
  });
});
