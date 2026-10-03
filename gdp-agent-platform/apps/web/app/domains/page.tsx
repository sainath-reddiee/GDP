import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

type Domain = {
  domain_id: string; domain_name: string; description: string | null; owner: string | null;
  active_flag: boolean; version: number; knowledge_items: number; target_tables: number;
};

export default async function Domains() {
  const { domains } = await api<{ domains: Domain[] }>("/api/domains");
  return (
    <div className="space-y-5">
      <h2>Domains</h2>
      <Card>
        <Table>
          <THead><TR><TH>Domain</TH><TH>Owner</TH><TH>Knowledge items</TH><TH>Target tables</TH><TH>Version</TH></TR></THead>
          <TBody>
            {domains.map((d) => (
              <TR key={d.domain_id}>
                <TD><div className="font-medium">{d.domain_name}</div><div className="text-muted-foreground">{d.description}</div></TD>
                <TD>{d.owner ?? "-"}</TD><TD>{d.knowledge_items}</TD><TD>{d.target_tables}</TD><TD>{d.version}</TD>
              </TR>
            ))}
            {domains.length === 0 && (
              <TR><TD colSpan={5} className="text-muted-foreground">No domains registered yet. The GDP domain pack is seeded in build phase 5.</TD></TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
