import { api } from "@/lib/api";
import type { IntakeForm, ShareList } from "@/lib/hub-types";
import type { DomainRow } from "@/app/onboarding/intent-types";
import { PageHeader } from "@/components/page-header";
import { OnboardWizard } from "./wizard";

export default async function NewSource({ searchParams }: { searchParams: { branch?: string } }) {
  const [form, shares, { domains }] = await Promise.all([
    api<{ form: IntakeForm }>("/api/hub/intake-form").then((r) => r.form).catch(() => null),
    api<ShareList>("/api/hub/sources/shares").catch(() => ({ shares: [], mounted: [] } as ShareList)),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Source Store" title="Onboard a source"
                  description="A Snowflake share lands straight away through a compressed Parquet stage. An external system is registered in the ingestion catalog for the pipelines to load." />
      <OnboardWizard initialBranch={searchParams.branch === "external" ? "external" : "share"} form={form} shares={shares}
                     domains={domains.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />
    </div>
  );
}
