import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsView } from "../SettingsView";
import * as api from "../../services/api";
import type { Settings } from "../../types";

vi.mock("../../services/api");

function makeSettings(overrides: Partial<Settings> = {}): Settings {
  return {
    llm_provider: "local",
    local_llm_model: "qwen2.5:7b",
    local_llm_base_url: "http://localhost:11434",
    cloud_llm_enabled: false,
    cloud_llm_model: null,
    allowed_directories: [],
    wake_on_lan: { configured: false, mac_address: null, broadcast: "255.255.255.255", port: 9, command: null },
    ...overrides,
  };
}

describe("SettingsView - provider toggle", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the currently active provider", async () => {
    vi.mocked(api.getSettingsOverview).mockResolvedValue(makeSettings());
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: true });
    vi.mocked(api.getUsageSummary).mockResolvedValue({
      today: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
      month: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
    });

    render(<SettingsView />);

    const localBtn = await screen.findByRole("button", { name: "🖥 Lokal" });
    expect(localBtn).toHaveClass("is-active");
    expect(screen.getByRole("button", { name: "☁ Cloud" })).not.toHaveClass("is-active");
  });

  it("clicking Cloud calls setActiveProvider and updates the display", async () => {
    vi.mocked(api.getSettingsOverview).mockResolvedValue(makeSettings());
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: true });
    vi.mocked(api.getUsageSummary).mockResolvedValue({
      today: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
      month: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
    });
    vi.mocked(api.setActiveProvider).mockResolvedValue({ provider: "cloud", cloud_configured: true });

    const user = userEvent.setup();
    render(<SettingsView />);

    const cloudBtn = await screen.findByRole("button", { name: "☁ Cloud" });
    await user.click(cloudBtn);

    expect(api.setActiveProvider).toHaveBeenCalledWith("cloud");
    await waitFor(() => expect(cloudBtn).toHaveClass("is-active"));
  });

  it("disables the Cloud button when no API key is configured", async () => {
    vi.mocked(api.getSettingsOverview).mockResolvedValue(makeSettings());
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: false });
    vi.mocked(api.getUsageSummary).mockResolvedValue({
      today: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
      month: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
    });

    render(<SettingsView />);

    const cloudBtn = await screen.findByRole("button", { name: "☁ Cloud" });
    expect(cloudBtn).toBeDisabled();
    expect(await screen.findByText("CLOUD_LLM_API_KEY")).toBeInTheDocument();
  });

  it("shows a provider switch error instead of silently failing", async () => {
    vi.mocked(api.getSettingsOverview).mockResolvedValue(makeSettings());
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: true });
    vi.mocked(api.getUsageSummary).mockResolvedValue({
      today: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
      month: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
    });
    vi.mocked(api.setActiveProvider).mockRejectedValue(new Error("Request failed (400): boom"));

    const user = userEvent.setup();
    render(<SettingsView />);

    const cloudBtn = await screen.findByRole("button", { name: "☁ Cloud" });
    await user.click(cloudBtn);

    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it("shows the cloud cost counter with the monthly budget", async () => {
    vi.mocked(api.getSettingsOverview).mockResolvedValue(makeSettings());
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: true });
    const cloud = { prompt_tokens: 120000, completion_tokens: 3000, total_tokens: 123000, request_count: 40, cost_usd: 0.0123 };
    vi.mocked(api.getUsageSummary).mockResolvedValue({
      today: { total: cloud, by_provider: { cloud } },
      month: { total: cloud, by_provider: { cloud } },
      cloud_monthly_budget_usd: 5,
    });
    render(<SettingsView />);
    expect(await screen.findByText("0,0123 $ von 5,00 $ Budget")).toBeInTheDocument();
  });

  it("says when the cloud is switched off", async () => {
    vi.mocked(api.getSettingsOverview).mockResolvedValue(makeSettings());
    vi.mocked(api.getActiveProvider).mockResolvedValue({ provider: "local", cloud_configured: false, cloud_status: "disabled" });
    vi.mocked(api.getUsageSummary).mockResolvedValue({
      today: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
      month: { total: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 }, by_provider: {} },
    });
    render(<SettingsView />);
    expect(await screen.findByText("CLOUD_LLM_ENABLED=true")).toBeInTheDocument();
  });
});
