import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProjectsView } from "../ProjectsView";
import * as api from "../../services/api";
import type { Project } from "../../types";

vi.mock("../../services/api");

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 1,
    name: "Cypher",
    path: "C:\\Cypher",
    description: null,
    technologies: ["Python", "React"],
    repository: null,
    notes: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("ProjectsView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no projects", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([]);
    render(<ProjectsView />);
    expect(await screen.findByText(/Noch keine Projekte/)).toBeInTheDocument();
  });

  it("renders an existing project with its tech stack", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([makeProject()]);
    render(<ProjectsView />);
    expect(await screen.findByText("Cypher")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("React")).toBeInTheDocument();
  });

  it("creates a new project", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([]);
    vi.mocked(api.createProject).mockResolvedValue(makeProject({ id: 2, name: "AlwaysMC", technologies: [] }));

    const user = userEvent.setup();
    render(<ProjectsView />);

    const input = await screen.findByPlaceholderText("Projektname...");
    await user.type(input, "AlwaysMC");
    await user.click(screen.getByRole("button", { name: "Hinzufügen" }));

    expect(api.createProject).toHaveBeenCalledWith(expect.objectContaining({ name: "AlwaysMC" }));
    expect(await screen.findByText("AlwaysMC")).toBeInTheDocument();
  });

  it("deletes a project", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([makeProject()]);
    vi.mocked(api.deleteProject).mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<ProjectsView />);

    await screen.findByText("Cypher");
    await user.click(screen.getByRole("button", { name: "Löschen" }));

    expect(api.deleteProject).toHaveBeenCalledWith(1);
  });
});
