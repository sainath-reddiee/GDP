import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { AuditEvent } from "@/lib/types";
import { AuditTable } from "@/components/audit-table";

export default async function Audit() {
  const { events } = await api<{ events: AuditEvent[] }>("/api/audit");
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Platform" title="Audit" description="Latest 200 workflow events across all runs, newest first." />
      <AuditTable events={events} showRun />
    </div>
  );
}
