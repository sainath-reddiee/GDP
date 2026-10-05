import { api } from "@/lib/api";
import { OnboardingForm } from "./form";
import type { DatabaseRow, TargetRow } from "./catalog-types";
import type { DomainRow } from "./intent-types";

export default async function Onboarding() {
  const [{ databases }, { targets }, { domains }] = await Promise.all([
    api<{ databases: DatabaseRow[] }>("/api/sources/databases"),
    api<{ targets: TargetRow[] }>("/api/targets"),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
  ]);
  return <OnboardingForm databases={databases} targets={targets} domains={domains} />;
}
