import type { MemoryOverview } from "../services/api";

/** The memory as a JSON file - readable, and enough to re-enter it elsewhere. */
export function memoryExport(data: MemoryOverview, now = new Date()): { name: string; json: string } {
  const day = now.toISOString().slice(0, 10);
  const json = JSON.stringify(
    {
      exported_at: now.toISOString(),
      core: data.core.map((item) => item.text),
      archive: data.archive.map((item) => item.text),
    },
    null,
    2
  );
  return { name: `cypher-gedaechtnis-${day}.json`, json };
}
