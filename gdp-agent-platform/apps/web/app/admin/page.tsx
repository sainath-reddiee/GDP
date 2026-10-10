import Link from "next/link";
import { Suspense } from "react";
import { Bot, LayoutDashboard, Plug, Rocket, Ruler, ShieldCheck, SlidersHorizontal, Sparkles } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import type { GovEvent, GovPolicy, GovPrivilege, GovRole, GovSettings, GovUser } from "../governance-actions";
import { AccessSection } from "./access-section";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import type { ModelsState, PlatformState, RulesState } from "./actions";
import { ToastProvider } from "./admin-ui";
import { DeployButton } from "./deploy-button";
import { ModelsSection } from "./models-section";
import { RateCardEditor } from "./rate-card";
import { RulesSection } from "./rules-section";
import { IntegrationsSection, type IntegrationView } from "./integrations-section";
import type { CodeRepo, PublishingStatus } from "../code/actions";
import type { JiraStatus } from "../jira/actions";
import type { AirflowEnv } from "../ops/actions";
import { SkillsSection } from "./skills-section";
import type { SkillBinding } from "../skills/types";
import { Panel, SectionSkeleton, Stat } from "./section";
import { StandardsSection } from "./standards-section";

const SECTIONS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, hint: "Session and platform at a glance" },
  { id: "models", label: "AI models", icon: Bot, hint: "Models in this account, the model per stage and the rate card" },
  { id: "access", label: "Access and governance", icon: ShieldCheck, hint: "Users, roles, privileges and approval policies" },
  { id: "integrations", label: "Integrations", icon: Plug, hint: "Connections to the client's tools, configured once and used by every run" },
  { id: "skills", label: "Skills per stage", icon: Sparkles, hint: "Which skills each pipeline stage loads, and in what order" },
  { id: "rules", label: "Rules", icon: SlidersHorizontal, hint: "Thresholds and hints" },
  { id: "standards", label: "Modeling standards", icon: Ruler, hint: "Naming and conventions" },
  { id: "deploy", label: "Deploy", icon: Rocket, hint: "Push code to Snowflake" },
] as const;
const MODEL_VIEWS = [
  { id: "models", label: "Models" },
  { id: "stages", label: "Model per stage" },
  { id: "rates", label: "Rate card" },
] as const;
type ModelView = (typeof MODEL_VIEWS)[number]["id"];
type SectionId = (typeof SECTIONS)[number]["id"];
const INTEGRATION_VIEWS: IntegrationView[] = ["repos", "jira", "airflow", "incidents", "cases"];

const unavailable = <p className="text-sm text-muted-foreground">Not available from the API yet. Restart the API after pulling.</p>;

export default async function Admin({ searchParams }: { searchParams?: { section?: string; view?: string } }) {
  const me = await whoami();
  if (!can(me, "ADMIN.VIEW")) {
    return (
      <div className="rounded-2xl border bg-card p-10 text-center shadow-sm">
        <h3 className="text-base font-semibold">Admin is not available to your role</h3>
        <p className="mt-1 text-sm text-muted-foreground">Ask a governance admin for a role with the ADMIN.VIEW privilege.</p>
      </div>
    );
  }
  const section = (SECTIONS.find((s) => s.id === searchParams?.section)?.id ?? "overview") as SectionId;
  const current = SECTIONS.find((s) => s.id === section)!;
  const view = (MODEL_VIEWS.find((v) => v.id === searchParams?.view)?.id ?? "models") as ModelView;
  return (
    <ToastProvider>
      <div className="space-y-5">
        <PageHeader eyebrow="Govern" title="Admin"
                    description="AI models and cost, rules, modeling standards and deployment. Changes are versioned and survive deploys." />
        <nav aria-label="Admin sections" className="flex gap-1 overflow-x-auto border-b">
          {SECTIONS.map((s) => (
            <Link key={s.id} href={`/admin?section=${s.id}`} aria-current={s.id === section ? "page" : undefined} title={s.hint}
                  className={cn("-mb-px flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors",
                    s.id === section ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
              <s.icon className="h-4 w-4 shrink-0" />
              {s.label}
            </Link>
          ))}
        </nav>
        <main className="min-w-0 space-y-4">
          <p className="text-xs text-muted-foreground">{current.hint}</p>
          {section === "models" && (
            <div role="tablist" className="inline-flex rounded-lg border bg-muted/40 p-0.5">
              {MODEL_VIEWS.map((v) => (
                <Link key={v.id} href={`/admin?section=models&view=${v.id}`} role="tab" aria-selected={v.id === view}
                      className={cn("rounded-md px-3 py-1 text-xs font-medium", v.id === view ? "bg-card shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                  {v.label}
                </Link>
              ))}
            </div>
          )}
          <Suspense key={`${section}:${view}:${searchParams?.view ?? ""}`} fallback={<SectionSkeleton />}>
            <Section id={section} view={view}
                     integrationView={INTEGRATION_VIEWS.find((v) => v === searchParams?.view) ?? "repos"} />
          </Suspense>
        </main>
      </div>
    </ToastProvider>
  );
}

async function Section({ id, view, integrationView }: { id: SectionId; view: ModelView; integrationView: IntegrationView }) {
  if (id === "overview") return <Overview />;
  if (id === "models") {
    const [models, platform, costs] = await Promise.all([
      api<ModelsState>("/api/config/models").then((data) => ({ data, error: undefined }))
        .catch((e: Error) => ({ data: null, error: e.message })),
      api<PlatformState>("/api/config/platform").catch(() => null),
      api<{ calibrated_rates?: Record<string, number> }>("/api/costs?group_by=model&limit=50").catch(() => null),
    ]);
    const settings = platform?.settings ?? {};
    const legacy = (settings.CREDITS_PER_MILLION_TOKENS?.value as Record<string, number>) ?? {};
    return (
      <div className="space-y-5">
        {view !== "rates" && <ModelsSection initial={models.data} error={models.error} view={view} />}
        {view === "rates" && platform && (
          <RateCardEditor
            rateCard={(settings.RATE_CARD?.value as Record<string, { input?: number; output?: number }>) ?? {}}
            fallback={Number(legacy.default ?? 0)} legacy={legacy}
            price={(settings.CREDIT_PRICE_USD?.value as number | null) ?? null}
            billed={costs?.calibrated_rates ?? {}}
            modelNames={(models.data?.models ?? []).filter((m) => m.available !== false).map((m) => m.name)} />
        )}
        {view === "rates" && <p className="text-xs text-muted-foreground">
          Spend by stage, model, run and day, and reconciling with Snowflake billing, are on{" "}
          <Link href="/audit?tab=cost" className="text-primary hover:underline">Audit &gt; Cost</Link>.
        </p>}
      </div>
    );
  }
  if (id === "integrations") {
    const [repos, domains, publishing, jira, airflow] = await Promise.all([
      api<{ repos: CodeRepo[]; ready: boolean }>("/api/code/repos").catch(() => null),
      api<{ domains: { domain_id: string; domain_name: string; active_flag: boolean }[] }>("/api/domains").catch(() => ({ domains: [] })),
      api<PublishingStatus>("/api/dbt/github").catch(() => ({ ready: false, config: null })),
      api<JiraStatus>("/api/jira/status").catch(() => null),
      // 404: the ops API is not deployed yet; other API errors are shown on the Airflow tab
      api<{ envs: AirflowEnv[] }>("/api/ops/envs").then((r) => ({ envs: r.envs as AirflowEnv[] | null, error: null as string | null }), (e: unknown) => {
        if (e instanceof ApiError) return { envs: e.status === 404 ? null : [], error: e.status === 404 ? null : e.message };
        throw e;
      }),
    ]);
    if (!repos) return unavailable;
    if (!repos.ready) return <p className="text-sm text-muted-foreground">Code repositories need the latest deploy (migration V024).</p>;
    return <IntegrationsSection repos={repos.repos} publishing={publishing} jira={jira} airflow={airflow.envs} airflowError={airflow.error} view={integrationView}
                                domains={domains.domains.filter((d) => d.active_flag).map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />;
  }
  if (id === "skills") {
    const data = await api<{ stages: string[]; bindings: SkillBinding[]; skills: string[] }>("/api/skills/bindings").catch(() => null);
    return data ? <SkillsSection stages={data.stages} bindings={data.bindings} skills={data.skills} /> : unavailable;
  }
  if (id === "rules") {
    const rules = await api<RulesState>("/api/config/rules").catch(() => null);
    return rules ? <RulesSection rules={rules} /> : unavailable;
  }
  if (id === "standards") {
    const platform = await api<PlatformState>("/api/config/platform").catch(() => null);
    return platform ? <StandardsSection platform={platform} /> : unavailable;
  }
  if (id === "access") {
    try {
      const [users, roles, privileges, policies, settings, events] = await Promise.all([
        api<{ users: GovUser[] }>("/api/governance/users"),
        api<{ roles: GovRole[] }>("/api/governance/roles"),
        api<{ privileges: GovPrivilege[] }>("/api/governance/privileges"),
        api<{ policies: GovPolicy[] }>("/api/governance/policies"),
        api<{ settings: GovSettings }>("/api/governance/settings"),
        api<{ events: GovEvent[] }>("/api/governance/events"),
      ]);
      return <AccessSection users={users.users} roles={roles.roles} privileges={privileges.privileges}
                            policies={policies.policies} settings={settings.settings} events={events.events} />;
    } catch (e) {
      return <p className="text-sm text-muted-foreground">{e instanceof Error ? e.message : "Governance is not available."}</p>;
    }
  }
  if (id === "deploy") {
    const me = await whoami();
    return (
      <Panel title="Deploy to Snowflake"
             description={<>Uploads the current services code and applies new migrations, procedures, the workflow graph, skills,
               search services and the agent, using your signed-in role ({me?.role}). Run it after pulling or changing code;
               it is safe to repeat because migrations already applied are skipped.</>}>
        <DeployButton />
      </Panel>
    );
  }
  return unavailable;
}

async function Overview() {
  type Summary = { total?: number; cost_30d?: { calls: number; credits?: number; estimated_cost: number; actual_credits?: number } | null };
  const [me, platform, rules, metrics] = await Promise.all([
    whoami(),
    api<PlatformState>("/api/config/platform").catch(() => null),
    api<RulesState>("/api/config/rules").catch(() => null),
    api<Summary>("/api/metrics/summary").catch(() => null),
  ]);
  const s = platform?.settings ?? {};
  const byStage = Object.keys((s.LLM_MODEL_BY_STAGE?.value as Record<string, string>) ?? {}).length;
  const rateCount = Object.keys((s.RATE_CARD?.value as Record<string, unknown>) ?? {}).length;
  const cost = metrics?.cost_30d;
  const go = (section: string, label: string) => (
    <Link href={`/admin?section=${section}`} className="text-xs text-primary hover:underline">{label}</Link>
  );
  return (
    <div className="space-y-5">
      <Panel title="Session" description="Who you are signed in as; Admin changes are recorded against this user."
             actions={<span className="rounded-full bg-muted px-2.5 py-1 text-[11px] text-muted-foreground">{me?.auth_mode === "pat" ? "Token sign-in" : "Developer sign-in"}</span>}>
        <dl className="grid gap-4 text-sm sm:grid-cols-3">
          <div><dt className="text-xs text-muted-foreground">User</dt><dd className="font-medium">{me?.user ?? "unknown"}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Role</dt><dd className="font-medium">{me?.role ?? "unknown"}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Agent</dt><dd className="font-medium">{me?.agent ?? "not deployed"}</dd></div>
        </dl>
      </Panel>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Default AI model" value={<span className="font-mono text-base">{String(s.LLM_MODEL?.value ?? "not set")}</span>}
              hint={<>{byStage ? `${byStage} stage override(s)` : "No stage overrides"} · {go("models&view=stages", "Manage")}</>} />
        <Stat label="AI credits, 30 days" value={cost ? (cost.credits ?? cost.estimated_cost).toFixed(2) : "-"}
              hint={<>{cost?.actual_credits ? `${cost.actual_credits.toFixed(2)} billed` : "Estimated"} · <Link href="/audit?tab=cost" className="text-xs text-primary hover:underline">Details</Link></>} />
        <Stat label="Rules changed" value={rules ? rules.overridden.length : "-"} hint={<>From the platform defaults · {go("rules", "Review")}</>} />
        <Stat label="Standards overrides" value={["GDP", "GENERIC"].reduce((n, k) => n + Object.keys((s[`MODELING_STANDARD.${k}`]?.value as Record<string, unknown>) ?? {}).length, 0)}
              hint={<>GDP and generic presets · {go("standards", "Review")}</>} />
      </div>
      {rateCount === 0 && (
        <Panel title="AI cost uses learned rates" description="No rate card is set, so estimates use the credits per million tokens this account was actually billed for each model, learned from Snowflake's Cortex usage on every reconcile. Set a rate card to use contracted rates instead."
               actions={<Link href="/admin?section=models&view=rates" className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted">Open rate card</Link>} />
      )}
    </div>
  );
}
