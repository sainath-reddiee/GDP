import Link from "next/link";
import type { RunSummary } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

export function statusVariant(status: string) {
  if (status === "FAILED") return "destructive" as const;
  if (status === "COMPLETED") return "success" as const;
  if (status === "AWAITING_REVIEW") return "warning" as const;
  return "secondary" as const;
}

export function RunTable({ runs }: { runs: RunSummary[] }) {
  return (
    <Card>
      <Table>
        <THead>
          <TR><TH>Name</TH><TH>Target</TH><TH>Stage</TH><TH>State</TH><TH>Status</TH><TH>Created by</TH><TH>Created</TH></TR>
        </THead>
        <TBody>
          {runs.map((r) => (
            <TR key={r.run_id}>
              <TD><Link href={`/runs/${r.run_id}`} className="font-medium text-primary hover:underline">{r.run_name}</Link></TD>
              <TD>{r.target_model ?? "-"}</TD>
              <TD>{r.current_stage ?? "-"}</TD>
              <TD className="font-mono text-xs">{r.current_state}</TD>
              <TD><Badge variant={statusVariant(r.status)}>{r.status}</Badge></TD>
              <TD>{r.created_by}</TD>
              <TD className="text-muted-foreground">{r.created_at.slice(0, 19)}</TD>
            </TR>
          ))}
          {runs.length === 0 && <TR><TD colSpan={7} className="text-muted-foreground">No runs yet.</TD></TR>}
        </TBody>
      </Table>
    </Card>
  );
}
