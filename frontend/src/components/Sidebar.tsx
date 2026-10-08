export type View = "voice" | "chat" | "tasks" | "memory" | "projects" | "settings";

interface Props {
  active: View;
  onChange: (v: View) => void;
  pendingTaskCount: number;
}

const NAV_ITEMS: { id: View; label: string }[] = [
  { id: "voice", label: "Voice" },
  { id: "chat", label: "Chat" },
  { id: "tasks", label: "Tasks" },
  { id: "memory", label: "Memory" },
  { id: "projects", label: "Projects" },
  { id: "settings", label: "Settings" },
];

export function Sidebar({ active, onChange, pendingTaskCount }: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__brand-dot" />
        CYPHER
      </div>
      <nav className="sidebar__nav">
        {NAV_ITEMS.map((item) => (
          <button key={item.id} className={`sidebar__nav-item ${active === item.id ? "is-active" : ""}`} onClick={() => onChange(item.id)}>
            {item.label}
            {item.id === "tasks" && pendingTaskCount > 0 && <span className="sidebar__nav-badge">{pendingTaskCount}</span>}
          </button>
        ))}
      </nav>
      <div className="sidebar__spacer" />
      <div className="sidebar__footer" />
    </aside>
  );
}
