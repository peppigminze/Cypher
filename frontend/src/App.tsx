import { useCallback, useEffect, useState } from "react";
import "./App.css";
import { Sidebar, type View } from "./components/Sidebar";
import { StatusBadge } from "./components/StatusBadge";
import { ChatView } from "./components/ChatView";
import { TasksView } from "./components/TasksView";
import { MemoryView } from "./components/MemoryView";
import { ProjectsView } from "./components/ProjectsView";
import { SettingsView } from "./components/SettingsView";
import { CypherVoiceView } from "./components/CypherVoiceView";
import { PairingView } from "./components/PairingView";
import { AUTH_REQUIRED_EVENT, ensureToken, type AuthState } from "./services/auth";
import { getStatus } from "./services/api";
import { usePolling } from "./hooks/usePolling";
import { useReminderNotifications } from "./hooks/useReminderNotifications";
import type { SystemStatus } from "./types";

const TITLES: Record<View, string> = {
  voice: "Voice",
  chat: "Chat",
  tasks: "Tasks",
  memory: "Memory",
  projects: "Projects",
  settings: "Settings",
};

export default function App() {
  const [auth, setAuth] = useState<AuthState | "checking">("checking");

  const checkAuth = useCallback(() => {
    setAuth("checking");
    void ensureToken().then(setAuth);
  }, []);

  useEffect(() => {
    let alive = true;
    void ensureToken().then((state) => alive && setAuth(state));
    const onAuthRequired = () => checkAuth();
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    return () => {
      alive = false;
      window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    };
  }, [checkAuth]);

  if (auth === "checking") {
    return <main className="pairing" aria-busy="true" />;
  }
  if (auth !== "ready") {
    return <PairingView offline={auth === "offline"} onPaired={() => setAuth("ready")} onRetry={checkAuth} />;
  }
  return <AuthenticatedApp />;
}

function AuthenticatedApp() {
  // "voice" is the new landing view (the full-screen orb). Every other
  // view is unchanged and still fully reachable - see the menu overlay
  // below - nothing about /api/messages or the existing views changed.
  const [view, setView] = useState<View>("voice");
  const [menuOpen, setMenuOpen] = useState(false);
  const [status, setStatus] = useState<SystemStatus | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await getStatus());
    } catch {
      setStatus((prev) => (prev ? { ...prev, pc_online: false } : prev));
    }
  }, []);

  usePolling(refreshStatus, 10000, []);
  useReminderNotifications();

  const navigate = useCallback((v: View) => {
    setView(v);
    setMenuOpen(false);
  }, []);

  if (view === "voice") {
    return (
      <>
        <CypherVoiceView status={status} onOpenMenu={() => setMenuOpen(true)} />
        {menuOpen && (
          <div className="voice-menu-overlay" onClick={() => setMenuOpen(false)}>
            <div className="voice-menu-drawer" onClick={(e) => e.stopPropagation()}>
              <Sidebar active={view} onChange={navigate} pendingTaskCount={status?.pending_messages ?? 0} />
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="shell">
      <Sidebar active={view} onChange={navigate} pendingTaskCount={status?.pending_messages ?? 0} />
      <div className="main">
        <header className="topbar">
          <span className="topbar__title">{TITLES[view]}</span>
          <StatusBadge status={status} />
        </header>
        <div className={`panel ${view === "chat" ? "panel--flush" : ""}`}>
          {view === "chat" && <ChatView />}
          {view === "tasks" && <TasksView />}
          {view === "memory" && <MemoryView />}
          {view === "projects" && <ProjectsView />}
          {view === "settings" && <SettingsView />}
        </div>
      </div>
    </div>
  );
}
