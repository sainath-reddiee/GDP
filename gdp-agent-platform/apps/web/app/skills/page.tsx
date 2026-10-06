import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

type Skill = { skill_id: string; skill_name: string; skill_type: string; version: string; stage_path: string; status: string; created_at: string };

export default async function Skills() {
  const { skills } = await api<{ skills: Skill[] }>("/api/skills");
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Skills"
                  description={`${skills.length} current playbooks the agents load before acting: profiling, mapping, STTM, data quality and dbt.`} />
      <Card>
        <Table>
          <THead><TR><TH>Skill</TH><TH>Type</TH><TH>Version</TH><TH>Status</TH><TH>Stage path</TH></TR></THead>
          <TBody>
            {skills.map((s) => (
              <TR key={s.skill_id}>
                <TD className="font-medium">{s.skill_name}</TD><TD>{s.skill_type}</TD><TD>{s.version}</TD>
                <TD><Badge variant={s.status === "ACTIVE" ? "success" : "secondary"}>{s.status}</Badge></TD>
                <TD className="font-mono text-xs">{s.stage_path}</TD>
              </TR>
            ))}
            {skills.length === 0 && (
              <TR><TD colSpan={5} className="text-muted-foreground">No skills are registered yet.</TD></TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
