import Link from "next/link";
import type { AuditEvent } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

export function AuditTable({ events, showRun = false }: { events: AuditEvent[]; showRun?: boolean }) {
  return (
    <Card>
      <Table>
        <THead>
          <TR>
            <TH>When</TH>{showRun && <TH>Run</TH>}<TH>From</TH><TH>To</TH><TH>Actor</TH><TH>Reason</TH>
          </TR>
        </THead>
        <TBody>
          {events.map((e) => (
            <TR key={e.event_id}>
              <TD className="whitespace-nowrap text-muted-foreground">{e.created_at.slice(0, 19)}</TD>
              {showRun && (
                <TD><Link href={`/runs/${e.run_id}`} className="text-primary hover:underline">{e.run_name}</Link></TD>
              )}
              <TD className="font-mono text-xs">{e.from_state ?? "-"}</TD>
              <TD className="font-mono text-xs">{e.to_state}</TD>
              <TD>
                <Badge variant={e.actor_type === "HUMAN" ? "warning" : "secondary"}>{e.actor_type}</Badge> {e.actor}
              </TD>
              <TD>{e.reason ?? ""}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}
