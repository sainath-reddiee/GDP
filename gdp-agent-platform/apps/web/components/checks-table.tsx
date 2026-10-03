import type { SourceOverview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

const VARIANT = { PASSED: "success", FAILED: "destructive", WARNING: "warning" } as const;

export function ChecksTable({ checks }: { checks: SourceOverview["checks"] }) {
  return (
    <Table>
      <THead><TR><TH>Check</TH><TH>Status</TH><TH>Detail</TH><TH>Remediation</TH></TR></THead>
      <TBody>
        {checks.map((c, i) => (
          <TR key={`${c.check_name}-${i}`}>
            <TD className="font-mono text-xs">{c.check_name}</TD>
            <TD><Badge variant={VARIANT[c.status]}>{c.status}</Badge></TD>
            <TD>{c.detail}</TD>
            <TD className="text-muted-foreground">{c.remediation ?? ""}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
