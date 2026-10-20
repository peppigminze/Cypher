import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TasksView } from "../TasksView";
import * as api from "../../services/api";
import type { Task } from "../../types";

vi.mock("../../services/api");

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 1,
    title: "Test-Aufgabe",
    description: null,
    notes: null,
    tags: [],
    status: "pending",
    priority: "medium",
    due_at: null,
    reminder_enabled: false,
    recurrence: "none",
    last_notified_at: null,
    created_at: new Date().toISOString(),
    completed_at: null,
    ...overrides,
  };
}

describe("TasksView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no tasks", async () => {
    vi.mocked(api.listTasks).mockResolvedValue([]);
    render(<TasksView />);
    expect(await screen.findByText(/Noch keine Aufgaben/)).toBeInTheDocument();
  });

  it("renders existing tasks", async () => {
    vi.mocked(api.listTasks).mockResolvedValue([makeTask({ title: "Einkaufen" })]);
    render(<TasksView />);
    expect(await screen.findByText("Einkaufen")).toBeInTheDocument();
  });

  it("shows a reminder bell for tasks with reminders enabled", async () => {
    vi.mocked(api.listTasks).mockResolvedValue([makeTask({ title: "Zahnarzt", reminder_enabled: true })]);
    render(<TasksView />);
    expect(await screen.findByText("🔔 Zahnarzt")).toBeInTheDocument();
  });

  it("creates a new task", async () => {
    vi.mocked(api.listTasks).mockResolvedValue([]);
    const created = makeTask({ id: 2, title: "Neue Aufgabe" });
    vi.mocked(api.createTask).mockResolvedValue(created);

    const user = userEvent.setup();
    render(<TasksView />);

    const input = await screen.findByPlaceholderText("Neue Aufgabe...");
    await user.type(input, "Neue Aufgabe");
    await user.click(screen.getByRole("button", { name: "Hinzufügen" }));

    expect(api.createTask).toHaveBeenCalledWith(expect.objectContaining({ title: "Neue Aufgabe", priority: "medium" }));
    expect(await screen.findByText("Neue Aufgabe")).toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("completes a task", async () => {
    const task = makeTask({ title: "Fertig machen" });
    vi.mocked(api.listTasks).mockResolvedValue([task]);
    vi.mocked(api.completeTask).mockResolvedValue({ ...task, status: "completed" });

    const user = userEvent.setup();
    render(<TasksView />);

    await screen.findByText("Fertig machen");
    await user.click(screen.getByRole("button", { name: "Erledigt" }));

    expect(api.completeTask).toHaveBeenCalledWith(1);
  });

  it("deletes a task", async () => {
    const task = makeTask({ title: "Löschbar" });
    vi.mocked(api.listTasks).mockResolvedValue([task]);
    vi.mocked(api.deleteTask).mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<TasksView />);

    await screen.findByText("Löschbar");
    await user.click(screen.getByRole("button", { name: "Löschen" }));

    expect(api.deleteTask).toHaveBeenCalledWith(1);
  });

  it("disables the add button while the title is empty", async () => {
    vi.mocked(api.listTasks).mockResolvedValue([]);
    render(<TasksView />);
    await screen.findByPlaceholderText("Neue Aufgabe...");
    expect(screen.getByRole("button", { name: "Hinzufügen" })).toBeDisabled();
  });
});
