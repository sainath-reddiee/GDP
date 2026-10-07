import Link from "next/link";
import { Suspense } from "react";
import { Bot, Coins, GitBranch, LayoutDashboard, Rocket, Ruler, SlidersHorizontal } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import type { ModelsState, PlatformState, RulesState } from "./actions";
import { ToastProvider } from "./admin-ui";
import { CostSection } from "./cost-section";
import { DeployButton } from "./deploy-button";
import { ModelsSection } from "./models-section";
import { RulesSection } from "./rules-section";
import { Panel, SectionSkeleton, Stat } from "./section";
import { StandardsSection } from "./standards-section";
import { WorkflowLanes, type GraphState, type GraphTransition } from "./workflow-lanes";

const SECTIONS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, hint: "Session and platform at a glance" },
  { id: "models", label: "AI models", icon: Bot, hint: "Default and per-stage models" },
  { id: "cost", label: "Cost and usage", icon: Coins, hint: "Credits, rate card, billing" },
  { id: "rules", label: "Rules", icon: SlidersHorizontal, hint: "Thresholds and hints" },
  { id: "standards", label: "Modeling standards", icon: Ruler, hint: "Naming and conventions" },
  { id: "deploy", label: "Deploy", icon: Rocket, hint: "Push code to Snowflake" },
  { id: "workflow", label: "Workflow", icon: GitBranch, hint: "States and transitions" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

const unavailable = <p className="text-sm text-muted-foreground">Not available from the API yet. Restart the API after pulling.</p>;

export default function Admin({ searchParams }: { searchParams?: { section?: string } }) {
  const section = (SECTIONS.find((s) => s.id === searchParams?.section)?.id ?? "overview") as SectionId;
  const current = SECTIONS.find((s) => s.id === section)!;
  return (
    <ToastProvider>
      <div className="space-y-5">
        <PageHeader eyebrow="Platform" title="Admin"
                    description="AI models and cost, rules and standards, deployment and the workflow every run follows. Changes are versioned and survive deploys." />
        <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
          <nav aria-label="Admin sections" className="lg:sticky lg:top-4 lg:self-start">
            <ul className="flex gap-1 overflow-x-auto lg:flex-col">
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <Link href={`/admin?section=${s.id}`} aria-current={s.id === section ? "page" : undefined}
                        className={cn("flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors",
                          s.id === section ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                    <s.icon className="h-4 w-4 shrink-0" />
                    {s.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <main className="min-w-0 space-y-4">
            <div>
              <h2 className="text-lg font-semibold">{current.label}</h2>
              <p className="text-xs text-muted-foreground">{current.hint}</p>
            </div>
            <Suspense key={section} fallback={<SectionSkeleton />}>
              <Section id={section} />
            </Suspense>
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}

async function Section({ id }: { id: SectionId }) {
  if (id === "overview") return <Overview />;
  if (id === "models") {
    const models = await api<ModelsState>("/api/config/models").then((data) => ({ data, error: undefined }))
      .catch((e: Error) => ({ data: null, error: e.message }));
    return <ModelsSection initial={models.data} error={models.error} />;
  }
  if (id === "cost") {
    const [platform, models] = await Promise.all([
      api<PlatformState>("/api/config/platform").catch(() => null),
      api<ModelsState>("/api/config/models").catch(() => null),
    ]);
    return <CostSection platform={platform}
                        modelNames={(models?.models ?? []).filter((m) => m.available !== false).map((m) => m.name)} />;
  }
  if (id === "rules") {
    const rules = await api<RulesState>("/api/config/rules").catch(() => null);
    return rules ? <RulesSection rules={rules} /> : unavailable;
  }
  if (id === "standards") {
    const platform = await api<PlatformState>("/api/config/platform").catch(() => null);
    return platform ? <StandardsSection platform={platform} /> : unavailable;
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
  const graph = await api<{ states: GraphState[]; transitions: GraphTransition[] }>("/api/admin/workflow")
    .catch(() => ({ states: [] as GraphState[], transitions: [] as GraphTransition[] }));
  const enabled = graph.states.filter((s) => s.enabled).length;
  return (
    <Panel title={`Workflow graph ${graph.states[0]?.graph_version ?? ""}`}
           description={`${graph.states.length} states (${enabled} enabled) and ${graph.transitions.length} transitions. Lanes follow the run order; a person icon marks a step that needs a human decision. Faded states belong to later phases and cannot be reached.`}>
      {graph.states.length ? <WorkflowLanes states={graph.states} transitions={graph.transitions} /> : unavailable}
    </Panel>
  );
}

async function Overview() {
  type Summary = { total?: number; cost_30d?: { calls: number; credits?: number; estimated_cost: number; actual_credits?: number } | null };
  const [me, platform, rules, metrics, graph] = await Promise.all([
    whoami(),
    api<PlatformState>("/api/config/platform").catch(() => null),
    api<RulesState>("/api/config/rules").catch(() => null),
    api<Summary>("/api/metrics/summary").catch(() => null),
    api<{ states: GraphState[] }>("/api/admin/workflow").catch(() => null),
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
              hint={<>{byStage ? `${byStage} stage override(s)` : "No stage overrides"} · {go("models", "Manage")}</>} />
        <Stat label="AI credits, 30 days" value={cost ? (cost.credits ?? cost.estimated_cost).toFixed(2) : "-"}
              hint={<>{cost?.actual_credits ? `${cost.actual_credits.toFixed(2)} billed` : "Estimated"} · {go("cost", "Details")}</>} />
        <Stat label="Rules changed" value={rules ? rules.overridden.length : "-"} hint={<>From the platform defaults · {go("rules", "Review")}</>} />
        <Stat label="Workflow" value={graph ? `${graph.states.filter((x) => x.enabled).length} states` : "-"}
              hint={<>{graph?.states[0]?.graph_version ?? ""} · {go("workflow", "View")}</>} />
      </div>
      {rateCount === 0 && (
        <Panel title="AI cost uses learned rates" description="No rate card is set, so estimates use the credits per million tokens this account was actually billed for each model, learned from Snowflake's Cortex usage on every reconcile. Set a rate card to use contracted rates instead."
               actions={<Link href="/admin?section=cost" className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted">Open rate card</Link>} />
      )}
    </div>
  );
}
