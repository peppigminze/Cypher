import { useCallback, useEffect, useState } from "react";
import {
  addMemoryItem,
  deleteConversationHistory,
  deleteMemoryItem,
  getMemoryOverview,
  updateMemoryItem,
  type MemoryItem,
  type MemoryLayer,
  type MemoryOverview,
} from "../services/api";

/**
 * Memory editor (M4): everything Cypher remembers, editable and deletable.
 *
 *   Kern-Gedächtnis  always part of Cypher's prompt - limited, shown with a
 *                    fill bar. "Merk dir ..." lands here first.
 *   Archiv           unlimited, searched per question; only matching
 *                    entries are used.
 *
 * Moving an entry between the two is "anpinnen" / "ins Archiv".
 */
export function MemoryView() {
  const [data, setData] = useState<MemoryOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [draftLayer, setDraftLayer] = useState<MemoryLayer>("core");
  const [editing, setEditing] = useState<{ item: MemoryItem; text: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Bumped after every change; the effect below then reloads the overview.
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    getMemoryOverview()
      .then((overview) => {
        if (!alive) return;
        setData(overview);
        setError(null);
      })
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [version]);

  async function run(action: () => Promise<unknown>, success?: string) {
    try {
      await action();
      setNotice(success ?? null);
      setError(null);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const add = () => {
    const text = draft.trim();
    if (!text) return;
    void run(async () => {
      await addMemoryItem(text, draftLayer);
      setDraft("");
    });
  };

  const saveEdit = () => {
    if (!editing) return;
    const text = editing.text.trim();
    if (!text) return;
    void run(async () => {
      await updateMemoryItem(editing.item, { text });
      setEditing(null);
    });
  };

  const usedPercent = data ? Math.min(100, Math.round((data.core_used_tokens / Math.max(1, data.core_max_tokens)) * 100)) : 0;

  const renderItem = (item: MemoryItem) => {
    const isEditing = editing?.item.layer === item.layer && editing.item.id === item.id;
    return (
      <li className="memory-item" key={`${item.layer}:${item.id}`}>
        {isEditing ? (
          <form
            className="memory-item__edit"
            onSubmit={(e) => {
              e.preventDefault();
              saveEdit();
            }}
          >
            <input
              className="text-input"
              value={editing.text}
              maxLength={300}
              autoFocus
              aria-label="Eintrag bearbeiten"
              onChange={(e) => setEditing({ item, text: e.target.value })}
              onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
            />
            <button type="submit" className="btn-primary">
              Speichern
            </button>
            <button type="button" className="icon-btn" onClick={() => setEditing(null)}>
              Abbrechen
            </button>
          </form>
        ) : (
          <>
            <span className="memory-item__text">{item.text}</span>
            <span className="memory-item__actions">
              <button type="button" className="icon-btn" onClick={() => setEditing({ item, text: item.text })}>
                Bearbeiten
              </button>
              <button
                type="button"
                className="icon-btn"
                title={item.layer === "core" ? "Nur noch bei passenden Fragen nutzen" : "Immer mitgeben"}
                onClick={() => void run(() => updateMemoryItem(item, { move_to: item.layer === "core" ? "archive" : "core" }))}
              >
                {item.layer === "core" ? "Ins Archiv" : "Anpinnen"}
              </button>
              <button
                type="button"
                className="icon-btn icon-btn--danger"
                onClick={() => {
                  if (window.confirm(`„${item.text}“ wirklich vergessen?`)) void run(() => deleteMemoryItem(item), "Gelöscht.");
                }}
              >
                Löschen
              </button>
            </span>
          </>
        )}
      </li>
    );
  };

  return (
    <div className="memory-view">
      <div className="list-header">
        <h1>Gedächtnis</h1>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <input
            className="text-input"
            placeholder="Was soll Cypher sich merken?"
            value={draft}
            maxLength={300}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Neuer Eintrag"
            style={{ minWidth: 240 }}
          />
          <select
            className="select-input"
            value={draftLayer}
            onChange={(e) => setDraftLayer(e.target.value as MemoryLayer)}
            aria-label="Ablage"
          >
            <option value="core">Kern-Gedächtnis</option>
            <option value="archive">Archiv</option>
          </select>
          <button type="submit" className="btn-primary" disabled={!draft.trim()}>
            Merken
          </button>
        </form>
      </div>

      {error && (
        <p className="memory-view__error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="memory-view__notice" role="status">
          {notice}
        </p>
      )}

      {data === null ? (
        <div className="empty-state">{error ? "Gedächtnis nicht erreichbar." : "Lade …"}</div>
      ) : (
        <>
          <section className="memory-section" aria-labelledby="core-title">
            <h2 id="core-title">Kern-Gedächtnis</h2>
            <p className="memory-section__hint">Steht in jedem Gespräch zur Verfügung.</p>
            <div
              className="memory-budget"
              role="meter"
              aria-valuemin={0}
              aria-valuemax={data.core_max_tokens}
              aria-valuenow={data.core_used_tokens}
              aria-label="Belegung Kern-Gedächtnis"
            >
              <div className="memory-budget__fill" style={{ width: `${usedPercent}%` }} />
            </div>
            <p className="memory-section__hint">{usedPercent} % belegt</p>
            {data.core.length === 0 ? (
              <p className="empty-state">Noch nichts. Sag „Merk dir, dass …“.</p>
            ) : (
              <ul className="memory-list">{data.core.map(renderItem)}</ul>
            )}
          </section>

          <section className="memory-section" aria-labelledby="archive-title">
            <h2 id="archive-title">Archiv</h2>
            <p className="memory-section__hint">Wird nur genutzt, wenn es zur Frage passt.</p>
            {data.archive.length === 0 ? (
              <p className="empty-state">Leer.</p>
            ) : (
              <ul className="memory-list">{data.archive.map(renderItem)}</ul>
            )}
          </section>

          <section className="memory-section">
            <h2>Gesprächsverlauf</h2>
            <p className="memory-section__hint">
              Die letzten Gespräche bleiben gespeichert, damit Cypher nach einem Neustart weiss, worum es ging.
            </p>
            <button
              type="button"
              className="icon-btn icon-btn--danger"
              onClick={() => {
                if (window.confirm("Gesamten gespeicherten Gesprächsverlauf löschen? (Das Gedächtnis oben bleibt.)"))
                  void run(() => deleteConversationHistory(), "Gesprächsverlauf gelöscht.");
              }}
            >
              Verlauf löschen
            </button>
          </section>
        </>
      )}
    </div>
  );
}
