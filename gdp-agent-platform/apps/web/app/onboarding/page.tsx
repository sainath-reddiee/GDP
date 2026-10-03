import { api } from "@/lib/api";
import { OnboardingForm } from "./form";
import type { DatabaseRow, TargetRow } from "./catalog-types";

export default async function Onboarding() {
  const [{ databases }, { targets }] = await Promise.all([
    api<{ databases: DatabaseRow[] }>("/api/sources/databases"),
    api<{ targets: TargetRow[] }>("/api/targets"),
  ]);
  return <OnboardingForm databases={databases} targets={targets} />;
}
