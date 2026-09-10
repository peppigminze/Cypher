import { useCallback, useState } from "react";
import type { MemoryEntry, MemoryType } from "../types";
import { deleteMemory, listMemory, saveMemory } from "../services/api";
import { usePolling } from "../hooks/usePolling";

const TYPE_LABEL: Record<MemoryType, string> = {
  fact: "Fakt",
  preference: "Vorliebe",
  project: "Projekt",
};

export function MemoryView() {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("");
  const [memoryType, setMemoryType] = useState<MemoryType>("fact");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setEntries(await listMemory());
    } catch {
      // keep showing last known list
    }
  }, []);

  usePolling(refresh, 8000, []);

  async function handleSave() {
    const c = content.trim();
    if (!c || busy) return;
    setBusy(true);
    try {
      const entry = await saveMemory(c, category.trim() || undefined, memoryType);
      setEntries((prev) => [entry, ...prev]);
      setContent("");
      setCategory("");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: number) {
    await deleteMemory(id);
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }

  return (
    <div>
      <div className="list-header">
        <h1>Memory</h1>
        <div className="inline-form">
          <input
            className="text-input"
            placeholder="Was soll Cypher sich merken?"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSave()}
            style={{ minWidth: 220 }}
          />
          <input
            className="text-input"
            placeholder="Kategorie (optional)"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            style={{ width: 140 }}
          />
          <select
            className="select-input"
            value={memoryType}
            onChange={(e) => setMemoryType(e.target.value as MemoryType)}
          >
            <option value="fact">Fakt</option>
            <option value="preference">Vorliebe</option>
            <option value="project">Projekt</option>
          </select>
          <button className="btn-primary" onClick={handleSave} disabled={busy || !content.trim()}>
            Speichern
          </button>
        </div>
      </div>

      {entries.length === 0 ? (
        <div className="empty-state">Noch keine Erinnerungen gespeichert.</div>
      ) : (
        <div className="card-list">
          {entries.map((m) => (
            <div className="card" key={m.id}>
              <div className="card__body">
                <p className="card__title">{m.content}</p>
                <div className="card__meta">
                  <span className="category-tag">{TYPE_LABEL[m.memory_type] ?? m.memory_type}</span>
                  {m.category && <span className="category-tag">{m.category}</span>}
                  <span>{new Date(m.updated_at).toLocaleDateString("de-CH")}</span>
                </div>
              </div>
              <div className="card__actions">
                <button className="icon-btn" onClick={() => handleDelete(m.id)}>
                  Löschen
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
