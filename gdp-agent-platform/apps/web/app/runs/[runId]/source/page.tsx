import { api, getRun } from "@/lib/api";
import type { CachedProfile, LandingTargets, SourceOverview } from "@/lib/types";
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
  const qs = `database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}`;
  const [catalog, cache, landing] = await Promise.all([
    database && schema
      ? api<{ tables: TableRow[] }>(`/api/catalog/tables?${qs}`).catch(() => ({ tables: [] as TableRow[] }))
      : { tables: [] as TableRow[] },
    database && schema
      ? api<{ profiles: CachedProfile[] }>(`/api/profiles?${qs}`).catch(() => ({ profiles: [] as CachedProfile[] }))
      : { profiles: [] as CachedProfile[] },
    api<LandingTargets>("/api/landing/targets").catch(() => null),
  ]);

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

  const current = state.current_state;
  const failedIn = current === "FAILED" ? state.failed_from_state : null;
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
      cachedProfiles={cache.profiles}
      landingTargets={landing}
      target={{
        landing_database: state.run.landing_database || landing?.default.landing_database || "",
        landing_schema: state.run.landing_schema || landing?.default.landing_schema || "LANDING",
        storage_type: state.run.storage_type || "MANAGED",
      }}
      canRegister={current === "CREATED"}
      canValidate={current === "SOURCE_REGISTERED" || failedIn === "ACCESS_VALIDATION"}
      canResumeLanding={["ACCESS_APPROVED", "LANDING_PENDING"].includes(current) || failedIn === "LANDING_RUNNING"}
      landed={current === "LANDING_COMPLETE"}
      failureReason={failedIn ? state.failure_reason : null}
    />
  );
}
