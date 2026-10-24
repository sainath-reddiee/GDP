import { guard } from "@/lib/os/session";
import { DomainScreen } from "@/app/domains/[id]/domain-screen";

export const dynamic = "force-dynamic";
export const metadata = { title: "Domain knowledge" };

/** A domain's knowledge in the Data OS: playbook, references, models, memory, rules, sources, lineage and versions. */
export default async function OsDomain({ params, searchParams }: { params: { id: string }; searchParams?: { tab?: string; layer?: string } }) {
  await guard("knowledge");
  return <DomainScreen id={params.id} searchParams={searchParams} />;
}
