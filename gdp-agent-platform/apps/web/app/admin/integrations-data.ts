import { api, ApiError } from "@/lib/api";
import type { DatadogStatus } from "../ops/datadog-actions";
import type { CodeRepo, PublishingStatus } from "../code/actions";
import type { JiraStatus } from "../jira/actions";
import type { AirflowEnv } from "../ops/actions";

/** What Platform settings, Integrations shows (code repositories, GitHub publishing, Jira, Airflow, Datadog); super
 *  admins only. null when the API cannot be read; notReady before migration V024. */
export async function loadIntegrations() {
  const [repos, domains, publishing, jira, airflow, datadog] = await Promise.all([
    api<{ repos: CodeRepo[]; ready: boolean }>("/api/code/repos").catch(() => null),
    api<{ domains: { domain_id: string; domain_name: string; active_flag: boolean }[] }>("/api/domains").catch(() => ({ domains: [] })),
    api<PublishingStatus>("/api/dbt/github").catch(() => ({ ready: false, config: null }) as PublishingStatus),
    api<JiraStatus>("/api/jira/status").catch(() => null),
    // 404: the ops API is not deployed yet; other API errors are shown on the Airflow tab
    api<{ envs: AirflowEnv[] }>("/api/ops/envs").then((r) => ({ envs: r.envs as AirflowEnv[] | null, error: null as string | null }), (e: unknown) => {
      if (e instanceof ApiError) return { envs: e.status === 404 ? null : [], error: e.status === 404 ? null : e.message };
      throw e;
    }),
    api<DatadogStatus>("/api/ops/datadog").catch(() => null),
  ]);
  if (!repos) return null;
  if (!repos.ready) return { notReady: true as const };
  return {
    repos: repos.repos, publishing, jira, airflow: airflow.envs, airflowError: airflow.error, datadog,
    domains: domains.domains.filter((d) => d.active_flag).map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name })),
  };
}
