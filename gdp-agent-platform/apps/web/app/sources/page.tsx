import { api } from "@/lib/api";
import type { SourceInventory, SourcesOverview } from "@/lib/types";
import type { DatabaseRow } from "@/app/onboarding/catalog-types";
import { SourcesHub } from "./sources-hub";

export default async function SourcesPage({ searchParams }: { searchParams: { source?: string } }) {
  const [overview, { databases }] = await Promise.all([
    api<SourcesOverview>("/api/sources/overview"),
    api<{ databases: DatabaseRow[] }>("/api/sources/databases").catch(() => ({ databases: [] as DatabaseRow[] })),
  ]);
  const selectedId = overview.sources.some((s) => s.source_system_id === searchParams.source)
    ? searchParams.source!
    : overview.sources[0]?.source_system_id ?? "";
  const inventory = selectedId
    ? await api<SourceInventory>(`/api/sources/${selectedId}/inventory`).catch(() => null)
    : null;
  return <SourcesHub initialOverview={overview} databases={databases} initialSelected={selectedId} initialInventory={inventory} />;
}
