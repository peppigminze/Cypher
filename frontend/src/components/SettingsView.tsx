import { useCallback, useEffect, useState } from "react";
import type { ActiveProviderInfo, LlmProviderName, Settings, UsageSummary } from "../types";
import { formatUsd } from "../utils/format";
import { getActiveProvider, getSettingsOverview, getUsageSummary, setActiveProvider } from "../services/api";
import { usePolling } from "../hooks/usePolling";

/** Rough "might be on a phone/PWA" signal for the Phase 7 cloud hint -
 * deliberately just a suggestion shown next to the toggle, never used
 * to switch anything automatically (that's an explicit non-goal: "Kosten
 * dürfen nie ungefragt entstehen"). Re-checked on resize so rotating a
 * tablet or resizing a window updates the hint without a reload. */
function useIsLikelyMobileContext(): boolean {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => {
      const standalone = window.matchMedia?.("(display-mode: standalone)").matches ?? false;
      const narrow = window.innerWidth < 768;
      setIsMobile(standalone || narrow);
    };
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  return isMobile;
}

export function SettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [copied, setCopied] = useState(false);
  const [activeProvider, setActiveProviderState] = useState<ActiveProviderInfo | null>(null);
  const [providerBusy, setProviderBusy] = useState(false);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const isLikelyMobile = useIsLikelyMobileContext();

  const refresh = useCallback(async () => {
    try {
      setSettings(await getSettingsOverview());
    } catch {
      // keep showing the last known settings
    }
    try {
      setActiveProviderState(await getActiveProvider());
    } catch {
      // keep showing the last known provider state
    }
    try {
      setUsage(await getUsageSummary());
    } catch {
      // usage is a nice-to-have readout - failing silently is fine
    }
  }, []);

  usePolling(refresh, 15000, []);

  async function handleProviderChange(next: LlmProviderName) {
    if (!activeProvider || activeProvider.provider === next || providerBusy) return;
    setProviderBusy(true);
    setProviderError(null);
    try {
      setActiveProviderState(await setActiveProvider(next));
    } catch (err) {
      setProviderError(err instanceof Error ? err.message : String(err));
    } finally {
      setProviderBusy(false);
    }
  }

  async function copyWakeCommand() {
    if (!settings?.wake_on_lan.command) return;
    try {
      await navigator.clipboard.writeText(settings.wake_on_lan.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard API unavailable - nothing we can do silently
    }
  }

  if (!settings) {
    return <div className="empty-state">Lade Einstellungen...</div>;
  }

  const wol = settings.wake_on_lan;

  return (
    <div className="settings">
      <section className="settings__section">
        <h2>LLM</h2>
        <dl className="settings__grid">
          <dt>Lokales Modell</dt>
          <dd>{settings.local_llm_model}</dd>
          <dt>Lokale URL</dt>
          <dd>{settings.local_llm_base_url}</dd>
          <dt>Cloud</dt>
          <dd>
            {settings.cloud_llm_enabled ? (
              <span className="pill status-completed">Erlaubt ({settings.cloud_llm_model}) - nur auf Wunsch oder nach Rückfrage</span>
            ) : (
              <span className="pill status-pending">Ausgeschaltet (CLOUD_LLM_ENABLED)</span>
            )}
          </dd>
        </dl>
      </section>

      <section className="settings__section">
        <h2>Sprachmodell: lokal oder Cloud</h2>
        <p className="settings__hint">
          Legt fest, ob Cypher (Sprache und Chat) mit dem lokalen Ollama-Modell oder über die Cloud antwortet. Bleibt nach einem Neustart
          erhalten und wird nie automatisch umgeschaltet.
        </p>

        {activeProvider ? (
          <>
            <div className="settings__provider-toggle" role="group" aria-label="LLM-Provider wählen">
              <button
                type="button"
                className={`settings__provider-btn ${activeProvider.provider === "local" ? "is-active" : ""}`}
                onClick={() => handleProviderChange("local")}
                disabled={providerBusy || activeProvider.provider === "local"}
              >
                🖥 Lokal
              </button>
              <button
                type="button"
                className={`settings__provider-btn ${activeProvider.provider === "cloud" ? "is-active" : ""}`}
                onClick={() => handleProviderChange("cloud")}
                disabled={providerBusy || activeProvider.provider === "cloud" || !activeProvider.cloud_configured}
                title={!activeProvider.cloud_configured ? "Cloud ist nicht eingerichtet (.env)" : undefined}
              >
                ☁ Cloud
              </button>
            </div>

            {!activeProvider.cloud_configured && (
              <p className="settings__hint">
                {activeProvider.cloud_status === "disabled" ? (
                  <>
                    Cloud ist ausgeschaltet - <code>CLOUD_LLM_ENABLED=true</code> in <code>.env</code> setzen.
                  </>
                ) : (
                  <>
                    Cloud ist nicht verfügbar - <code>CLOUD_LLM_API_KEY</code> fehlt in <code>.env</code>.
                  </>
                )}
              </p>
            )}
            {providerError && <p className="settings__hint settings__hint--error">{providerError}</p>}
            {isLikelyMobile && activeProvider.provider === "local" && (
              <p className="settings__hint">
                Hinweis: Du wirkst gerade auf einem mobilen/PWA-Kontext unterwegs - der lokale PC ist dann evtl. nicht erreichbar. Cloud
                könnte hier zuverlässiger laufen (manuell umschalten, kein Automatismus).
              </p>
            )}
          </>
        ) : (
          <p className="settings__hint">Lade Provider-Status...</p>
        )}

        {usage && (
          <dl className="settings__grid" style={{ marginTop: 14 }}>
            <dt>Token-Nutzung heute</dt>
            <dd>{usage.today.total.total_tokens.toLocaleString("de-DE")}</dd>
            <dt>Token-Nutzung diesen Monat</dt>
            <dd>{usage.month.total.total_tokens.toLocaleString("de-DE")}</dd>
            <dt>Cloud-Kosten heute</dt>
            <dd>{formatUsd(usage.today.by_provider.cloud?.cost_usd ?? 0)}</dd>
            <dt>Cloud-Kosten diesen Monat</dt>
            <dd>
              {formatUsd(usage.month.by_provider.cloud?.cost_usd ?? 0)}
              {usage.cloud_monthly_budget_usd ? ` von ${formatUsd(usage.cloud_monthly_budget_usd)} Budget` : " (kein Budget gesetzt)"}
            </dd>
          </dl>
        )}
      </section>

      <section className="settings__section">
        <h2>Sicherheit</h2>
        <dl className="settings__grid">
          <dt>Erlaubte Verzeichnisse</dt>
          <dd>
            {settings.allowed_directories.length === 0 ? (
              <span className="pill status-failed">Keine - Datei-/Terminal-Tools deaktiviert</span>
            ) : (
              <ul className="settings__list">
                {settings.allowed_directories.map((dir) => (
                  <li key={dir}>{dir}</li>
                ))}
              </ul>
            )}
          </dd>
        </dl>
        <p className="settings__hint">
          Änderungen an Sicherheitseinstellungen (erlaubte Verzeichnisse, Tokens) erfolgen nur in der <code>.env</code>-Datei plus Neustart
          von Backend/Agent - nie über diese Seite.
        </p>
      </section>

      <section className="settings__section">
        <h2>Wake-on-LAN</h2>
        {wol.configured ? (
          <>
            <dl className="settings__grid">
              <dt>MAC-Adresse</dt>
              <dd>{wol.mac_address}</dd>
              <dt>Broadcast</dt>
              <dd>
                {wol.broadcast}:{wol.port}
              </dd>
            </dl>
            <p className="settings__hint">
              Da Backend/Agent auf diesem PC laufen, können sie sich nicht selbst aufwecken. Führe diesen Befehl von einem{" "}
              <strong>anderen Gerät im selben Netzwerk</strong> aus:
            </p>
            <div className="settings__code-row">
              <code className="settings__code">{wol.command}</code>
              <button className="icon-btn" onClick={copyWakeCommand}>
                {copied ? "Kopiert!" : "Kopieren"}
              </button>
            </div>
          </>
        ) : (
          <p className="settings__hint">
            Nicht konfiguriert. Trage <code>WAKE_ON_LAN_MAC</code> in der <code>.env</code> ein (MAC-Adresse via <code>ipconfig /all</code>{" "}
            finden).
          </p>
        )}
      </section>
    </div>
  );
}
