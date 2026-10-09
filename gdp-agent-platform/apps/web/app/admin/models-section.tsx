"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2, Play, RefreshCw, Search, Star, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { loadModels, savePlatformSetting, testModel, type AccountModel, type ModelTestResult, type ModelsState } from "./actions";
import { DirtyBar, useToast } from "./admin-ui";
import { Panel } from "./section";

const STAGE_INFO: Record<string, string> = {
  PROFILING: "Column notes and the AI review of profiles",
  MAPPING: "Source to target mapping proposals and assist",
  STTM: "Source to target mapping documents",
  SODA: "Data quality checks",
  QA: "QA review and sign-off notes",
  DBT: "dbt model review and enhance",
  KNOWLEDGE: "Domain knowledge, packs and answers",
  SUGGESTIONS: "Inline suggestions across stages",
  COPILOT: "The copilot assistant on every page",
  MODELING: "Designs the target data model in Mapping",
};
const FAMILY_LABEL: Record<string, string> = {
  claude: "Anthropic", openai: "OpenAI", llama: "Meta Llama", mistral: "Mistral", deepseek: "DeepSeek",
  snowflake: "Snowflake", google: "Google", xai: "xAI", qwen: "Qwen", other: "Other",
};
const SOURCE_LABEL: Record<string, string> = {
  cortex: "Cortex", account: "Account model", inference_profile: "Inference profile", config: "Configured",
};

/** Models this account can use, the platform default, a test per model and per-stage overrides. */
export function ModelsSection({ initial, error }: { initial: ModelsState | null; error?: string }) {
  const router = useRouter();
  const toast = useToast();
  const [data, setData] = useState<ModelsState | null>(initial);
  const [query, setQuery] = useState("");
  const [onlyAvailable, setOnlyAvailable] = useState(true);
  const [tests, setTests] = useState<Record<string, ModelTestResult | "running">>({});
  const [stages, setStages] = useState<Record<string, string>>(initial?.by_stage ?? {});
  const [pending, start] = useTransition();

  const models = data?.models ?? [];
  const available = models.filter((m) => m.available !== false);
  const shown = models.filter((m) => (!onlyAvailable || m.available !== false)
    && (!query || m.name.toLowerCase().includes(query.toLowerCase())));
  const groups = useMemo(() => {
    const out: Record<string, AccountModel[]> = {};
    for (const m of shown) (out[m.family] ??= []).push(m);
    return Object.entries(out).sort(([a], [b]) => (a === "claude" ? -1 : b === "claude" ? 1 : a.localeCompare(b)));
  }, [shown]);
  const saved = data?.by_stage ?? {};
  const dirty = (data?.stages ?? []).filter((s) => (stages[s] ?? "") !== (saved[s] ?? "")).length;

  const refresh = () => start(async () => {
    const r = await loadModels(true);
    if (r.ok) { setData(r.data); toast("ok", `${r.data.models.length} models found for this account.`); }
    else toast("error", r.error);
  });

  const runTest = async (name: string) => {
    setTests((t) => ({ ...t, [name]: "running" }));
    const r = await testModel(name);
    const result: ModelTestResult = r.ok ? r.data : { model: name, ok: false, error: r.error, latency_ms: 0 };
    setTests((t) => ({ ...t, [name]: result }));
    toast(result.ok ? "ok" : "error", result.ok ? `${name} answered in ${(result.latency_ms / 1000).toFixed(1)}s.` : `${name}: ${result.error}`);
  };

  const makeDefault = (name: string) => start(async () => {
    const r = await savePlatformSetting("LLM_MODEL", name);
    if (!r.ok) { toast("error", r.error); return; }
    setData((d) => (d ? { ...d, default: name } : d));
    toast("ok", `${name} is now the default model.`);
    router.refresh();
  });

  const saveStages = () => start(async () => {
    const clean = Object.fromEntries(Object.entries(stages).filter(([, v]) => v));
    const r = await savePlatformSetting("LLM_MODEL_BY_STAGE", clean);
    if (!r.ok) { toast("error", r.error); return; }
    setData((d) => (d ? { ...d, by_stage: clean } : d));
    toast("ok", "Stage models saved. New AI calls use them right away.");
  });

  if (!data) {
    return (
      <Panel title="AI models" description="The model list could not be read from Snowflake.">
        <p className="text-sm text-destructive">{error ?? "Restart the API after pulling, then reload."}</p>
      </Panel>
    );
  }

  return (
    <div className="space-y-5">
      <Panel
        title="Models in this account"
        description={data.allowlist
          ? `The account allows ${data.allowlist.length} model(s) (CORTEX_MODELS_ALLOWLIST). Others are shown as blocked.`
          : "Every Cortex model in your region is allowed for this account. Test one to confirm it answers before you rely on it."}
        actions={
          <Button size="sm" variant="outline" disabled={pending} onClick={refresh}>
            <RefreshCw className={cn("h-3.5 w-3.5", pending && "animate-spin")} /> Refresh list
          </Button>
        }>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="relative w-full max-w-xs">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search models" className="pl-8" aria-label="Search models" />
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={onlyAvailable} onChange={(e) => setOnlyAvailable(e.target.checked)} />
            Allowed models only ({available.length} of {models.length})
          </label>
        </div>
        {data.warnings.length > 0 && (
          <p className="mb-3 rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
            Some lookups were not allowed for this role, so the list may be partial: {data.warnings[0].split(":")[0]}
          </p>
        )}
        <div className="space-y-5">
          {groups.map(([family, items]) => (
            <div key={family}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{FAMILY_LABEL[family] ?? family}</p>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {items.map((m) => {
                  const t = tests[m.name];
                  const isDefault = m.name === data.default;
                  const blocked = m.available === false;
                  const usedBy = Object.entries(saved).filter(([, v]) => v === m.name).map(([k]) => k);
                  return (
                    <div key={m.name} className={cn("rounded-xl border p-3 transition-colors",
                      isDefault ? "border-primary/50 bg-primary/5" : "hover:border-primary/30", blocked && "opacity-60")}>
                      <div className="flex items-start justify-between gap-2">
                        <p className="min-w-0 break-all font-mono text-sm font-medium">{m.name}</p>
                        {isDefault
                          ? <Badge><Star className="h-3 w-3" /> Default</Badge>
                          : blocked ? <Badge variant="destructive">Blocked</Badge> : <Badge variant="success">Allowed</Badge>}
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {SOURCE_LABEL[m.source] ?? m.source}{usedBy.length ? ` · used by ${usedBy.join(", ").toLowerCase()}` : ""}
                      </p>
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <Button size="sm" variant="outline" disabled={blocked || t === "running"} onClick={() => runTest(m.name)}>
                          {t === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Test
                        </Button>
                        {!isDefault && (
                          <Button size="sm" variant="ghost" disabled={blocked || pending} onClick={() => makeDefault(m.name)}>Make default</Button>
                        )}
                        {t && t !== "running" && (
                          <span className={cn("flex items-center gap-1 text-[11px]", t.ok ? "text-success" : "text-destructive")}>
                            {t.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                            {t.ok ? `${(t.latency_ms / 1000).toFixed(1)}s` : "Failed"}
                          </span>
                        )}
                      </div>
                      {t && t !== "running" && !t.ok && <p className="mt-2 text-[11px] text-destructive">{t.error}</p>}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {groups.length === 0 && <p className="text-sm text-muted-foreground">No models match.</p>}
        </div>
      </Panel>

      <Panel title="Model per stage"
             description="Each AI step uses the default unless you pick a model for it. Use a stronger model where judgement matters (mapping, QA) and a faster one for high-volume steps.">
        <div className="divide-y rounded-xl border">
          {data.stages.map((stage) => {
            const value = stages[stage] ?? "";
            const changed = value !== (saved[stage] ?? "");
            return (
              <div key={stage} className="grid items-center gap-2 px-4 py-3 sm:grid-cols-[180px_1fr_260px]">
                <span className="text-sm font-medium capitalize">{stage.toLowerCase()}</span>
                <span className="text-xs text-muted-foreground">{STAGE_INFO[stage] ?? ""}</span>
                <select value={value} aria-label={`${stage} model`}
                        onChange={(e) => setStages((s) => ({ ...s, [stage]: e.target.value }))}
                        className={cn("h-9 rounded-md border bg-background px-2 text-sm", changed && "border-primary ring-2 ring-primary/20")}>
                  <option value="">Default ({data.default})</option>
                  {available.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                  {value && !available.some((m) => m.name === value) && <option value={value}>{value} (not in list)</option>}
                </select>
              </div>
            );
          })}
        </div>
      </Panel>
      <DirtyBar count={dirty} pending={pending} onSave={saveStages} onDiscard={() => setStages(saved)} what="stage model" />
    </div>
  );
}
