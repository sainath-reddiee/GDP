import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { AuditTable } from "@/components/audit-table";

export default async function Audit() {
  const { events } = await api<{ events: AuditEvent[] }>("/api/audit");
  return (
    <div className="space-y-5">
      <h2>Audit</h2>
      <p className="text-sm text-muted-foreground">Latest 200 workflow events across all runs, newest first.</p>
      <AuditTable events={events} showRun />
    </div>
  );
}
