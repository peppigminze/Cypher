import { useEffect, useState } from "react";
import { getPairing, type PairingInfo } from "../../services/api";

type Load = { state: "loading" } | { state: "ready"; info: PairingInfo } | { state: "remote" } | { state: "error"; message: string };

/**
 * "Handy koppeln": on the PC this shows a QR code / link the phone opens once
 * (the token then stays on the phone). On the phone itself the backend refuses
 * to hand out the token (403), so this only says the device is paired.
 */
export function DevicesPanel() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [showCode, setShowCode] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    getPairing()
      .then((info) => alive && setLoad({ state: "ready", info }))
      .catch((err: Error & { status?: number }) => {
        if (!alive) return;
        setLoad(err.status === 403 ? { state: "remote" } : { state: "error", message: err.message });
      });
    return () => {
      alive = false;
    };
  }, []);

  if (load.state === "loading") return <p className="panel-loading">Lade Kopplungsdaten …</p>;
  if (load.state === "error") {
    return (
      <p className="panel-error" role="alert">
        {load.message}
      </p>
    );
  }
  if (load.state === "remote") {
    return (
      <div className="sys-panel">
        <p>Dieses Gerät ist mit Cypher gekoppelt.</p>
        <p className="sys-panel__meta">Neue Geräte koppelst du direkt am PC: Menü → Handy.</p>
      </div>
    );
  }

  const { info } = load;
  const primary = info.links[0];
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="sys-panel devices">
      {primary ? (
        <>
          <h3>Mit dem Handy scannen</h3>
          <div className="devices__pair">
            {info.qr && <img className="devices__qr" src={info.qr} alt="QR-Code mit dem Kopplungslink" width={225} height={225} />}
            <div className="devices__steps">
              <ol>
                <li>Auf dem Handy Tailscale einschalten (gleiches Konto wie am PC).</li>
                <li>QR-Code mit der Kamera scannen oder den Link öffnen.</li>
                <li>Im Browser-Menü „Zum Startbildschirm hinzufügen“ wählen.</li>
              </ol>
              <p className="devices__link">{primary.url.replace(/#pair=.*/, "")}</p>
              <button type="button" className="btn-secondary" onClick={() => copy(primary.url)}>
                {copied ? "Kopiert" : "Link kopieren"}
              </button>
              {!primary.microphone && <p className="sys-panel__meta">Ohne HTTPS geht auf dem Handy nur der Chat, nicht das Mikrofon.</p>}
            </div>
          </div>
          <p className="sys-panel__meta">Der Link enthält deinen Zugangscode. Nur auf eigenen Geräten öffnen.</p>
        </>
      ) : (
        <>
          <h3>Noch kein Weg zum Handy</h3>
          <p>
            Cypher ist im Moment nur auf diesem PC erreichbar.{" "}
            {info.tailscale.installed
              ? "Tailscale ist installiert. Gib einmal in einer Eingabeaufforderung ein:"
              : "Am einfachsten geht es mit Tailscale (kostenlos, privat). Installiere es auf PC und Handy, dann einmal:"}
          </p>
          <pre className="devices__cmd">{info.serve_command}</pre>
          <p className="sys-panel__meta">Danach erscheint hier ein QR-Code. Anleitung: docs/MOBILE.md.</p>
        </>
      )}

      <h3>Code von Hand eingeben</h3>
      <p className="sys-panel__meta">Falls Scannen nicht klappt: Seite auf dem Handy öffnen und diesen Kopplungscode eingeben.</p>
      <div className="devices__code">
        <code aria-label="Kopplungscode">{showCode ? info.token : "•".repeat(Math.min(info.token.length, 24))}</code>
        <button type="button" className="btn-secondary" onClick={() => setShowCode((v) => !v)}>
          {showCode ? "Verbergen" : "Anzeigen"}
        </button>
      </div>
    </div>
  );
}
