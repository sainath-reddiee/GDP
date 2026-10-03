import { api, getRun } from "@/lib/api";
import type { SourceOverview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ChecksTable } from "@/components/checks-table";
import { RegisterSourceForm } from "./register-form";
import { ObjectSelector } from "./object-selector";

export default async function SourcePage({ params }: { params: { runId: string } }) {
  const [state, overview] = await Promise.all([
    getRun(params.runId),
    api<SourceOverview>(`/api/runs/${params.runId}/source`),
  ]);

  if (state.current_state === "CREATED") {
    const { databases } = await api<{ databases: { database_name: string; type: string; comment: string | null }[] }>("/api/sources/databases");
    return <RegisterSourceForm runId={params.runId} databases={databases} targetModel={state.run.target_model} />;
  }

  const { source, objects, checks } = overview;
  if (!source) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No source on record</CardTitle>
          <CardDescription>
            This run was advanced manually before source registration existed, so it has no registered source.
            Cancel it from the overview and start a new onboarding.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const editable = state.current_state === "SOURCE_REGISTERED";
  const lastAttempt = checks.length ? checks.filter((c) => c.checked_at === checks[0].checked_at) : [];

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {source?.source_system_name} <Badge variant="outline">{source?.source_type}</Badge>
          </CardTitle>
          <CardDescription>
            Source {source?.source_database}.{source?.source_schema} · target {state.run.target_model ?? "not set"} · {objects.length} objects discovered
          </CardDescription>
        </CardHeader>
      </Card>

      {editable ? (
        <ObjectSelector runId={params.runId} objects={objects} />
      ) : (
        <Card>
          <CardHeader><CardTitle>Selected objects</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Object</TH><TH>Type</TH><TH>Rows (estimate)</TH></TR></THead>
              <TBody>
                {objects.filter((o) => o.selected_flag).map((o) => (
                  <TR key={o.object_name}><TD>{o.object_name}</TD><TD>{o.object_type}</TD><TD>{o.row_count_estimate ?? "-"}</TD></TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {editable && lastAttempt.some((c) => c.status === "FAILED") && (
        <Card>
          <CardHeader>
            <CardTitle>Last access check failed</CardTitle>
            <CardDescription>Fix the selection or ask an admin to grant access, then validate again.</CardDescription>
          </CardHeader>
          <CardContent><ChecksTable checks={lastAttempt} /></CardContent>
        </Card>
      )}
    </>
  );
}
