import { useCallback, useEffect, useState } from "react";
import "./App.css";
import "./styles/hud.css";
import { CypherVoiceView } from "./components/CypherVoiceView";
import { PairingView } from "./components/PairingView";
import { SystemMenu } from "./components/system/SystemMenu";
import type { MenuSection } from "./components/system/menuSections";
import { useReminderNotifications } from "./hooks/useReminderNotifications";
import { AUTH_REQUIRED_EVENT, ensureToken, type AuthState } from "./services/auth";

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
  // One screen: the voice view. Everything else lives in the system menu
  // (menu button or M), so talking to Cypher is never more than a tap away.
  const [menuSection, setMenuSection] = useState<MenuSection | null>(null);
  useReminderNotifications();

  const openMenu = useCallback(() => setMenuSection((current) => current ?? "chat"), []);
  const closeMenu = useCallback(() => setMenuSection(null), []);

  return (
    <>
      <CypherVoiceView onOpenMenu={openMenu} menuOpen={menuSection !== null} />
      {menuSection && <SystemMenu section={menuSection} onSelect={setMenuSection} onClose={closeMenu} />}
    </>
  );
}
