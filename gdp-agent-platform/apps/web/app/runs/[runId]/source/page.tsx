import { api, getRun } from "@/lib/api";
import type { SourceOverview } from "@/lib/types";
import type { OnboardingIntent } from "@/app/onboarding/intent-types";
import type { TableRow } from "@/app/onboarding/catalog-types";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SourceStudio } from "./source-studio";

export default async function SourcePage({ params }: { params: { runId: string } }) {
  const [state, overview, intentWrap] = await Promise.all([
    getRun(params.runId),
    api<SourceOverview>(`/api/runs/${params.runId}/source`),
    api<{ intent: OnboardingIntent | null }>(`/api/runs/${params.runId}/intent`).catch(() => ({ intent: null })),
  ]);
  const intent = intentWrap.intent;
  const database = intent?.source.database || state.run.source_database || "";
  const schema = intent?.source.schema || state.run.source_schema || "";
  const catalog = database && schema
    ? await api<{ tables: TableRow[] }>(
        `/api/catalog/tables?database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}`,
      ).catch(() => ({ tables: [] as TableRow[] }))
    : { tables: [] as TableRow[] };

  if (!database || !schema) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Source is not on the plan yet</CardTitle>
          <CardDescription>
            Start from New onboarding and pick the source catalog there. This stage maps those
            tables — it does not ask you to browse the catalog again.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <SourceStudio
      runId={params.runId}
      sourceName={overview.source?.source_system_name || intent?.source.source_system_name || ""}
      sourceType={overview.source?.source_type || intent?.source.source_type || "SNOWFLAKE_DATABASE"}
      database={database}
      schema={schema}
      intent={intent}
      overview={overview}
      initialTables={catalog.tables}
      canRegister={state.current_state === "CREATED"}
      canValidate={state.current_state === "SOURCE_REGISTERED"}
    />
  );
}
