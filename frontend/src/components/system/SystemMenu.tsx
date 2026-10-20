import { useEffect, useRef, type ReactNode } from "react";
import { ChatView } from "../ChatView";
import { MemoryView } from "../MemoryView";
import { ProjectsView } from "../ProjectsView";
import { SettingsView } from "../SettingsView";
import { TasksView } from "../TasksView";
import { AuditPanel } from "./AuditPanel";
import { DevicesPanel } from "./DevicesPanel";
import { DiagnosticsPanel } from "./DiagnosticsPanel";
import { LogsPanel } from "./LogsPanel";
import { MENU_SECTIONS, type MenuSection } from "./menuSections";

function content(section: MenuSection): ReactNode {
  switch (section) {
    case "chat":
      return <ChatView />;
    case "memory":
      return <MemoryView />;
    case "tasks":
      return <TasksView />;
    case "projects":
      return <ProjectsView />;
    case "devices":
      return <DevicesPanel />;
    case "diagnostics":
      return <DiagnosticsPanel />;
    case "audit":
      return <AuditPanel />;
    case "logs":
      return <LogsPanel />;
    case "settings":
      return <SettingsView />;
  }
}

interface Props {
  section: MenuSection;
  onSelect: (section: MenuSection) => void;
  onClose: () => void;
}

/**
 * The hidden menu: everything that isn't talking to Cypher. Opens over the
 * voice view (menu button or the M key), closes with Esc, the X or a click
 * beside it. A modal dialog: focus moves in, Tab stays inside, and focus
 * returns to the menu button afterwards.
 */
export function SystemMenu({ section, onSelect, onClose }: Props) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      } else if (event.key === "Tab" && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        const list = Array.from(focusable).filter((el) => !el.hasAttribute("disabled"));
        if (list.length === 0) return;
        const first = list[0];
        const last = list[list.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, [onClose]);

  const current = MENU_SECTIONS.find((s) => s.id === section)!;
  return (
    <div className="sysmenu" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sysmenu__dialog" role="dialog" aria-modal="true" aria-labelledby="sysmenu-title" ref={dialogRef}>
        <nav className="sysmenu__nav" aria-label="Bereiche">
          <p className="sysmenu__brand">CYPHER · SYSTEM</p>
          {MENU_SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`sysmenu__item ${s.id === section ? "is-active" : ""}`}
              aria-current={s.id === section ? "page" : undefined}
              onClick={() => onSelect(s.id)}
            >
              <span className="sysmenu__label">{s.label}</span>
              <span className="sysmenu__hint">{s.hint}</span>
            </button>
          ))}
        </nav>
        <section className="sysmenu__content">
          <header className="sysmenu__header">
            <h2 id="sysmenu-title">{current.label}</h2>
            <button type="button" className="sysmenu__close" onClick={onClose} aria-label="Menü schliessen (Esc)" data-autofocus>
              ×
            </button>
          </header>
          <div className="sysmenu__body">{content(section)}</div>
        </section>
      </div>
    </div>
  );
}
