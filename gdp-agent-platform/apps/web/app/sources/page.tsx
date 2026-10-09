import { api } from "@/lib/api";
import type { CatalogInventory, ProfileStoreRow, SourcesOverview } from "@/lib/types";
import type { DatabaseRow } from "@/app/onboarding/catalog-types";
import type { DomainRow } from "@/app/onboarding/intent-types";
import { SourcesHub } from "./sources-hub";

const inventoryOf = (t: { database: string; schema: string }) =>
  api<CatalogInventory>(
    `/api/catalog/inventory?database=${encodeURIComponent(t.database)}&schema=${encodeURIComponent(t.schema)}`,
  ).catch(() => null);

export default async function SourcesPage({ searchParams }: { searchParams: { db?: string; schema?: string; mode?: string } }) {
  // An explicit ?db=&schema= wins and its inventory loads alongside everything else; otherwise reopen the schema
  // profiled most recently. Nothing is preselected when nothing was profiled yet.
  const explicit = searchParams.db && searchParams.schema
    ? { database: searchParams.db.toUpperCase(), schema: searchParams.schema.toUpperCase() }
    : null;
  const [overview, { profiles }, { databases }, { domains }, explicitInventory] = await Promise.all([
    api<SourcesOverview>("/api/sources/overview").catch(() => null),
    api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store").catch(() => ({ profiles: [] as ProfileStoreRow[] })),
    api<{ databases: DatabaseRow[] }>("/api/sources/databases").catch(() => ({ databases: [] as DatabaseRow[] })),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
    explicit ? inventoryOf(explicit) : Promise.resolve(null),
  ]);
  const target = explicit
    ?? (profiles[0] ? { database: profiles[0].database_name, schema: profiles[0].schema_name } : null);
  const inventory = explicit ? explicitInventory : target ? await inventoryOf(target) : null;
  return (
    <SourcesHub initialOverview={overview} initialStore={profiles} databases={databases} domains={domains}
                initialTarget={target} initialInventory={inventory}
                initialMode={searchParams.mode === "external" || searchParams.mode === "store" ? searchParams.mode : "snowflake"} />
  );
}
