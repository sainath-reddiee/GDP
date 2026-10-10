import Link from "next/link";
import { api, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import type { ChangeRequest } from "../governance-actions";
import { RequestList } from "./request-list";

const SCOPES: { key: string; label: string; privilege?: string }[] = [
  { key: "inbox", label: "Waiting for me" },
  { key: "mine", label: "My requests" },
  { key: "all", label: "All requests", privilege: "APPROVAL.VIEW" },
];

export default async function ApprovalsPage({ searchParams }: { searchParams: { scope?: string } }) {
  const me = await whoami();
  const scopes = SCOPES.filter((s) => !s.privilege || can(me, s.privilege));
  const scope = scopes.find((s) => s.key === searchParams.scope)?.key ?? "inbox";
  let data: { requests: ChangeRequest[]; user: string } | null = null;
  let error = "";
  try {
    data = await api<{ requests: ChangeRequest[]; user: string }>(`/api/governance/requests?scope=${scope}`);
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not load requests.";
  }
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Govern" title="Approvals"
                  description="Changes that need another role's approval. Approving applies the change with your access; every decision is logged." />
      <nav role="tablist" className="flex gap-1 border-b">
        {scopes.map((s) => (
          <Link key={s.key} href={`/approvals?scope=${s.key}`} role="tab" aria-selected={s.key === scope}
                className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                              s.key === scope ? "border-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {s.label}
          </Link>
        ))}
      </nav>
      {error ? (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">{error}</p>
      ) : (
        <RequestList requests={data?.requests ?? []} scope={scope} me={data?.user ?? ""} />
      )}
    </div>
  );
}
