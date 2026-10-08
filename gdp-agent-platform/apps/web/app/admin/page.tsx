import Link from "next/link";
import { Suspense } from "react";
import { Bot, LayoutDashboard, Rocket, Ruler, SlidersHorizontal } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import type { ModelsState, PlatformState, RulesState } from "./actions";
import { ToastProvider } from "./admin-ui";
import { DeployButton } from "./deploy-button";
import { ModelsSection } from "./models-section";
import { RateCardEditor } from "./rate-card";
import { RulesSection } from "./rules-section";
import { Panel, SectionSkeleton, Stat } from "./section";
import { StandardsSection } from "./standards-section";

const SECTIONS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, hint: "Session and platform at a glance" },
  { id: "models", label: "AI models", icon: Bot, hint: "Default and per-stage models, and their credit rates" },
  { id: "rules", label: "Rules", icon: SlidersHorizontal, hint: "Thresholds and hints" },
  { id: "standards", label: "Modeling standards", icon: Ruler, hint: "Naming and conventions" },
  { id: "deploy", label: "Deploy", icon: Rocket, hint: "Push code to Snowflake" },
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
                    description="AI models and cost, rules, modeling standards and deployment. Changes are versioned and survive deploys." />
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
        <ModelsSection initial={models.data} error={models.error} />
        {platform && (
          <RateCardEditor
            rateCard={(settings.RATE_CARD?.value as Record<string, { input?: number; output?: number }>) ?? {}}
            fallback={Number(legacy.default ?? 0)} legacy={legacy}
            price={(settings.CREDIT_PRICE_USD?.value as number | null) ?? null}
            billed={costs?.calibrated_rates ?? {}}
            modelNames={(models.data?.models ?? []).filter((m) => m.available !== false).map((m) => m.name)} />
        )}
        <p className="text-xs text-muted-foreground">
          Spend by stage, model, run and day, and reconciling with Snowflake billing, are on{" "}
          <Link href="/audit?tab=cost" className="text-primary hover:underline">Audit &gt; Cost</Link>.
        </p>
      </div>
    );
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
              hint={<>{byStage ? `${byStage} stage override(s)` : "No stage overrides"} · {go("models", "Manage")}</>} />
        <Stat label="AI credits, 30 days" value={cost ? (cost.credits ?? cost.estimated_cost).toFixed(2) : "-"}
              hint={<>{cost?.actual_credits ? `${cost.actual_credits.toFixed(2)} billed` : "Estimated"} · <Link href="/audit?tab=cost" className="text-xs text-primary hover:underline">Details</Link></>} />
        <Stat label="Rules changed" value={rules ? rules.overridden.length : "-"} hint={<>From the platform defaults · {go("rules", "Review")}</>} />
        <Stat label="Standards overrides" value={["GDP", "GENERIC"].reduce((n, k) => n + Object.keys((s[`MODELING_STANDARD.${k}`]?.value as Record<string, unknown>) ?? {}).length, 0)}
              hint={<>GDP and generic presets · {go("standards", "Review")}</>} />
      </div>
      {rateCount === 0 && (
        <Panel title="AI cost uses learned rates" description="No rate card is set, so estimates use the credits per million tokens this account was actually billed for each model, learned from Snowflake's Cortex usage on every reconcile. Set a rate card to use contracted rates instead."
               actions={<Link href="/admin?section=models" className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted">Open rate card</Link>} />
      )}
    </div>
  );
}
