/** The system menu's sections (order = order in the menu). */
export type MenuSection = "chat" | "memory" | "tasks" | "projects" | "devices" | "diagnostics" | "audit" | "logs" | "settings";

export const MENU_SECTIONS: { id: MenuSection; label: string; hint: string }[] = [
  { id: "chat", label: "Chat", hint: "Schreiben statt sprechen" },
  { id: "memory", label: "Gedächtnis", hint: "Was Cypher über dich weiss" },
  { id: "tasks", label: "Aufgaben", hint: "To-dos und Erinnerungen" },
  { id: "projects", label: "Projekte", hint: "Git-Projekte auf dem PC" },
  { id: "devices", label: "Handy", hint: "Handy koppeln (QR-Code)" },
  { id: "diagnostics", label: "Diagnose", hint: "Status, Latenzen, Kontext" },
  { id: "audit", label: "Audit-Log", hint: "Was Cypher getan hat" },
  { id: "logs", label: "Logs", hint: "Technische Protokolle" },
  { id: "settings", label: "Einstellungen", hint: "Modell, Cloud, Kosten" },
];
