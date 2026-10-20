import { useCallback, useState } from "react";
import type { Project } from "../types";
import { createProject, deleteProject, listProjects } from "../services/api";
import { usePolling } from "../hooks/usePolling";

export function ProjectsView() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [technologies, setTechnologies] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setProjects(await listProjects());
    } catch {
      // keep showing last known list
    }
  }, []);

  usePolling(refresh, 15000, []);

  async function handleCreate() {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      const project = await createProject({
        name: n,
        path: path.trim() || undefined,
        technologies: technologies
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      });
      setProjects((prev) => [...prev, project].sort((a, b) => a.name.localeCompare(b.name)));
      setName("");
      setPath("");
      setTechnologies("");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: number) {
    await deleteProject(id);
    setProjects((prev) => prev.filter((p) => p.id !== id));
  }

  return (
    <div>
      <div className="list-header">
        <h1>Projects</h1>
        <div className="inline-form">
          <input
            className="text-input"
            placeholder="Projektname..."
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <input
            className="text-input"
            placeholder="Pfad (optional)"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            style={{ minWidth: 200 }}
          />
          <input
            className="text-input"
            placeholder="Tech-Stack, kommagetrennt"
            value={technologies}
            onChange={(e) => setTechnologies(e.target.value)}
            style={{ minWidth: 180 }}
          />
          <button className="btn-primary" onClick={handleCreate} disabled={busy || !name.trim()}>
            Hinzufügen
          </button>
        </div>
      </div>

      {projects.length === 0 ? (
        <div className="empty-state">
          Noch keine Projekte registriert. Cypher kann sich dann per Chat/Tool an Pfad, Tech-Stack und Notizen erinnern.
        </div>
      ) : (
        <div className="card-list">
          {projects.map((p) => (
            <div className="card" key={p.id}>
              <div className="card__body">
                <p className="card__title">{p.name}</p>
                <div className="card__meta">
                  {p.path && <span>{p.path}</span>}
                  {p.technologies.map((tech) => (
                    <span className="category-tag" key={tech}>
                      {tech}
                    </span>
                  ))}
                </div>
                {p.description && <p className="card__meta">{p.description}</p>}
              </div>
              <div className="card__actions">
                <button className="icon-btn" onClick={() => handleDelete(p.id)}>
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
