import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryView } from "../MemoryView";
import { memoryExport } from "../../utils/memoryExport";
import * as api from "../../services/api";

vi.mock("../../services/api");

const overview: api.MemoryOverview = {
  core: [{ layer: "core", id: "c1", text: "Silvan hat freitags Berufsschule." }],
  archive: [{ layer: "archive", id: "7", text: "Altes Projekt: Wetterstation" }],
  core_used_tokens: 100,
  core_max_tokens: 400,
};

describe("MemoryView (memory editor)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows both layers and the core budget", async () => {
    vi.mocked(api.getMemoryOverview).mockResolvedValue(overview);
    render(<MemoryView />);
    expect(await screen.findByText("Silvan hat freitags Berufsschule.")).toBeInTheDocument();
    expect(screen.getByText("Altes Projekt: Wetterstation")).toBeInTheDocument();
    expect(screen.getByText("25 % belegt")).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
  });

  it("edits an entry in place", async () => {
    vi.mocked(api.getMemoryOverview).mockResolvedValue(overview);
    vi.mocked(api.updateMemoryItem).mockResolvedValue({ layer: "core", id: "c1", text: "Silvan hat montags Berufsschule." });
    render(<MemoryView />);
    await screen.findByText("Silvan hat freitags Berufsschule.");
    await userEvent.click(screen.getAllByRole("button", { name: "Bearbeiten" })[0]);
    const input = screen.getByRole("textbox", { name: "Eintrag bearbeiten" });
    await userEvent.clear(input);
    await userEvent.type(input, "Silvan hat montags Berufsschule.{Enter}");
    expect(api.updateMemoryItem).toHaveBeenCalledWith(overview.core[0], { text: "Silvan hat montags Berufsschule." });
  });

  it("pins an archive entry and deletes after confirmation", async () => {
    vi.mocked(api.getMemoryOverview).mockResolvedValue(overview);
    vi.mocked(api.updateMemoryItem).mockResolvedValue({ layer: "core", id: "c2", text: "Altes Projekt: Wetterstation" });
    vi.mocked(api.deleteMemoryItem).mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<MemoryView />);
    await screen.findByText("Altes Projekt: Wetterstation");
    await userEvent.click(screen.getByRole("button", { name: "Anpinnen" }));
    expect(api.updateMemoryItem).toHaveBeenCalledWith(overview.archive[0], { move_to: "core" });
    await userEvent.click(screen.getAllByRole("button", { name: "Löschen" })[0]);
    expect(api.deleteMemoryItem).toHaveBeenCalledWith(overview.core[0]);
  });

  it("shows the backend's German error (core memory full)", async () => {
    vi.mocked(api.getMemoryOverview).mockResolvedValue(overview);
    vi.mocked(api.addMemoryItem).mockRejectedValue(new Error("Das Kern-Gedächtnis ist voll."));
    render(<MemoryView />);
    await screen.findByText("Silvan hat freitags Berufsschule.");
    await userEvent.type(screen.getByRole("textbox", { name: "Neuer Eintrag" }), "Noch ein Fakt{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Das Kern-Gedächtnis ist voll.");
  });
});

describe("memory export", () => {
  it("writes both layers as plain text lists with a dated file name", () => {
    const { name, json } = memoryExport(
      {
        core: [{ layer: "core", id: "c1", text: "Ich heisse Silvan" }],
        archive: [{ layer: "archive", id: "7", text: "Lieblingsessen: Pizza" }],
        core_used_tokens: 5,
        core_max_tokens: 400,
      },
      new Date("2026-10-08T12:00:00Z")
    );
    expect(name).toBe("cypher-gedaechtnis-2026-10-08.json");
    expect(JSON.parse(json)).toEqual({
      exported_at: "2026-10-08T12:00:00.000Z",
      core: ["Ich heisse Silvan"],
      archive: ["Lieblingsessen: Pizza"],
    });
  });
});
