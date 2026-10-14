import { PageHeader } from "@/components/page-header";
import { LineageExplorer } from "./explorer";

/** End-to-end lineage: Source, Silver pre-MDM, MDM, Silver post-MDM, Gold, at table and column level. */
export default function LineagePage({ searchParams }: { searchParams: { object?: string; column?: string; direction?: string; q?: string } }) {
  const direction = searchParams.direction === "up" || searchParams.direction === "down" ? searchParams.direction : "both";
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Model and build" title="Lineage"
                  description="How data moves from a source through Silver (pre-MDM), Reltio's cross-reference and golden records (post-MDM) to Gold. Pick a table, and a column for column-level lineage. Built from landed tables, approved mappings (STTM), the mastered-domain contracts and gold generation." />
      <LineageExplorer initial={{ q: searchParams.q ?? "", object: searchParams.object ?? "", column: searchParams.column ?? null, direction }} />
    </div>
  );
}
