import Link from "next/link";
import type { AuditEvent } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

/** MAPPING_REVIEW -> "mapping review" */
export function stateLabel(state: string | null | undefined) {
  return state ? state.replace(/_/g, " ").toLowerCase() : "-";
}

/** Snowflake timestamps ("2026-10-07 13:54:12.123 -0700") shown in the viewer's local time. */
export function localTime(value: string) {
  const d = new Date(value.replace(" ", "T").replace(/ ([+-]\d{2})(\d{2})$/, "$1:$2"));
  return Number.isNaN(d.getTime()) ? value.slice(0, 19) : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

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
              <TD className="whitespace-nowrap text-muted-foreground" title={e.created_at}>{localTime(e.created_at)}</TD>
              {showRun && (
                <TD><Link href={`/runs/${e.run_id}`} className="text-primary hover:underline">{e.run_name}</Link></TD>
              )}
              <TD className="text-xs" title={e.from_state ?? ""}>{stateLabel(e.from_state)}</TD>
              <TD className="text-xs" title={e.to_state}>{stateLabel(e.to_state)}</TD>
              <TD>
                <Badge variant={e.actor_type === "HUMAN" ? "warning" : "secondary"}>{e.actor_type}</Badge> {e.actor}
              </TD>
              <TD>{e.reason ?? ""}</TD>
            </TR>
          ))}
          {events.length === 0 && (
            <TR><TD colSpan={showRun ? 6 : 5} className="py-6 text-center text-muted-foreground">No events match.</TD></TR>
          )}
        </TBody>
      </Table>
    </Card>
  );
}
