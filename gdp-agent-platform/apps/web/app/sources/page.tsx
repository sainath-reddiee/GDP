import { api } from "@/lib/api";
import type { CatalogInventory, ProfileStoreRow, SourcesOverview } from "@/lib/types";
import type { DatabaseRow } from "@/app/onboarding/catalog-types";
import type { DomainRow } from "@/app/onboarding/intent-types";
import { SourcesHub } from "./sources-hub";

export default async function SourcesPage({ searchParams }: { searchParams: { db?: string; schema?: string } }) {
  const [overview, { profiles }, { databases }, { domains }] = await Promise.all([
    api<SourcesOverview>("/api/sources/overview").catch(() => null),
    api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store").catch(() => ({ profiles: [] as ProfileStoreRow[] })),
    api<{ databases: DatabaseRow[] }>("/api/sources/databases").catch(() => ({ databases: [] as DatabaseRow[] })),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
  ]);
  // An explicit ?db=&schema= wins; otherwise reopen the schema profiled most recently. Nothing is preselected
  // when nothing was profiled yet, so the user starts from the catalog pickers.
  const target = searchParams.db && searchParams.schema
    ? { database: searchParams.db.toUpperCase(), schema: searchParams.schema.toUpperCase() }
    : profiles[0] ? { database: profiles[0].database_name, schema: profiles[0].schema_name } : null;
  const inventory = target
    ? await api<CatalogInventory>(
        `/api/catalog/inventory?database=${encodeURIComponent(target.database)}&schema=${encodeURIComponent(target.schema)}`,
      ).catch(() => null)
    : null;
  return (
    <SourcesHub initialOverview={overview} initialStore={profiles} databases={databases} domains={domains}
                initialTarget={target} initialInventory={inventory} />
  );
}
