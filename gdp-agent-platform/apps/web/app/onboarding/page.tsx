import { api } from "@/lib/api";
import { OnboardingForm } from "./form";
import type { DatabaseRow, TargetRow } from "./catalog-types";
import type { DomainRow } from "./intent-types";
import type { SourceConnection } from "@/lib/types";

export default async function Onboarding() {
  const [{ databases }, { targets }, { domains }, { sources }] = await Promise.all([
    api<{ databases: DatabaseRow[] }>("/api/sources/databases"),
    api<{ targets: TargetRow[] }>("/api/targets"),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
    api<{ sources: SourceConnection[] }>("/api/sources").catch(() => ({ sources: [] as SourceConnection[] })),
  ]);
  return <OnboardingForm databases={databases} targets={targets} domains={domains} connections={sources} />;
}
