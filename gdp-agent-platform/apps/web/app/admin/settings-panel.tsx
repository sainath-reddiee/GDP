"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, RotateCcw, Save } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { savePlatformSetting, saveRules, type PlatformState, type RulesState } from "./actions";

type Tab = "rules" | "ai" | "catalog" | "standards";

const RULE_GROUPS: [string, string][] = [
  ["quality.", "Data quality checks"], ["profile.", "Profiling"], ["relationships.", "Relationships"],
  ["joins.", "Joins"], ["mapping.", "Mapping"], ["domain.", "Domain detection"], ["ui.", "Confidence bands shown in pages"],
  ["hints.", "Name hints (second-line evidence after the values)"],
];
const CATALOG_LABELS: Record<string, string> = {
  hidden_target_tables: "Hidden target tables", hidden_target_databases: "Hidden target databases",
  hidden_target_schemas: "Hidden target schemas", hidden_target_ids: "Hidden target ids",
  hidden_domain_names: "Hidden domain names", strip_tokens: "Tokens stripped from suggested source names",
};

const toText = (v: unknown) => (Array.isArray(v) ? v.join(", ") : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? ""));

function parseLike(sample: unknown, text: string): unknown {
  if (typeof sample === "number") return text.trim() === "" ? sample : Number(text);
  if (Array.isArray(sample)) return text.split(",").map((s) => s.trim()).filter(Boolean);
  if (typeof sample === "object" && sample !== null) return JSON.parse(text || "{}");
  return text;
}

/** Platform settings: rules and thresholds, the AI model and cost rates, catalog display lists and modeling
 *  standards. Every value shows its default; changes are versioned in CORE.PLATFORM_CONFIG. */
export function SettingsPanel({ rules, platform }: { rules: RulesState; platform: PlatformState }) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("rules");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, start] = useTransition();

  const [ruleText, setRuleText] = useState<Record<string, string>>(
    () => Object.fromEntries(Object.entries(rules.rules).map(([k, v]) => [k, toText(v)])));
  const changedRules = useMemo(() => Object.keys(rules.defaults).filter((k) => ruleText[k] !== toText(rules.defaults[k])),
    [ruleText, rules.defaults]);

  const s = platform.settings;
  const [model, setModel] = useState(String(s.LLM_MODEL?.value ?? ""));
  const [rates, setRates] = useState(JSON.stringify(s.CREDITS_PER_MILLION_TOKENS?.value ?? {}, null, 2));
  const [catalog, setCatalog] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.keys(CATALOG_LABELS).map((k) => [k,
      ((s.CATALOG_DISPLAY?.value as Record<string, string[]> | undefined)?.[k] ?? []).join("\n")])));
  const [overrides, setOverrides] = useState<Record<string, string>>({
    GDP: JSON.stringify(s["MODELING_STANDARD.GDP"]?.value ?? {}, null, 2),
    GENERIC: JSON.stringify(s["MODELING_STANDARD.GENERIC"]?.value ?? {}, null, 2),
  });

  const done = (msg: string) => { setNotice(msg); router.refresh(); };

  const saveAllRules = () => start(async () => {
    setError(""); setNotice("");
    const out: Record<string, unknown> = {};
    try {
      for (const key of changedRules) out[key] = parseLike(rules.defaults[key], ruleText[key]);
    } catch {
      setError("A value is not valid JSON."); return;
    }
    const r = await saveRules(out);
    if (!r.ok) { setError(r.error); return; }
    done(`Saved. ${r.data.overridden.length} rule(s) differ from the defaults; stages pick them up within a minute.`);
  });

  const saveSetting = (key: string, value: unknown, reset = false) => start(async () => {
    setError(""); setNotice("");
    const r = await savePlatformSetting(key, value, reset);
    if (!r.ok) { setError(r.error); return; }
    if (reset) {
      const v = r.data.settings[key]?.value;
      if (key === "LLM_MODEL") setModel(String(v));
      if (key === "CREDITS_PER_MILLION_TOKENS") setRates(JSON.stringify(v, null, 2));
    }
    done(reset ? `${key} reset to the default.` : `${key} saved.`);
  });

  const parseJson = (text: string): unknown | undefined => {
    try { return JSON.parse(text); } catch { setError("Not valid JSON."); return undefined; }
  };

  const Customised = ({ k }: { k: string }) =>
    s[k]?.customised ? <Badge variant="outline">changed by {s[k]?.changed_by} · {s[k]?.changed_at?.slice(0, 16)}</Badge> : <Badge variant="secondary">default</Badge>;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap gap-1 rounded-lg border p-1 text-sm">
        {([["rules", "Rules and thresholds"], ["ai", "AI model and cost"], ["catalog", "Catalog display"],
           ["standards", "Modeling standards"]] as const).map(([value, label]) => (
          <button key={value} type="button" onClick={() => setTab(value)} aria-pressed={tab === value}
                  className={cn("rounded-md px-3 py-1.5", tab === value ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>
            {label}
          </button>
        ))}
      </div>

      {tab === "rules" && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Platform-wide values. A domain can override them again for its own runs. Lists are comma-separated.
          </p>
          {RULE_GROUPS.map(([prefix, title]) => {
            const keys = Object.keys(rules.defaults).filter((k) => k.startsWith(prefix));
            if (!keys.length) return null;
            return (
              <div key={prefix} className="rounded-xl border">
                <p className="border-b bg-muted/40 px-3 py-2 text-sm font-semibold">{title}</p>
                <div className="divide-y">
                  {keys.map((k) => {
                    const changed = ruleText[k] !== toText(rules.defaults[k]);
                    return (
                      <div key={k} className="grid items-center gap-2 px-3 py-2 sm:grid-cols-[260px_1fr_auto]">
                        <span className="font-mono text-xs">{k}</span>
                        <input value={ruleText[k] ?? ""} onChange={(e) => setRuleText((t) => ({ ...t, [k]: e.target.value }))}
                               aria-label={k} className={cn("h-8 rounded-md border bg-background px-2 font-mono text-xs", changed && "border-primary")} />
                        <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
                          default {toText(rules.defaults[k]).slice(0, 40) || "none"}
                          {changed && (
                            <button type="button" title="Back to the default" className="text-primary"
                                    onClick={() => setRuleText((t) => ({ ...t, [k]: toText(rules.defaults[k]) }))}>
                              <RotateCcw className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          <div className="flex items-center gap-3">
            <Button disabled={pending} onClick={saveAllRules}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save rules
            </Button>
            <span className="text-xs text-muted-foreground">{changedRules.length} differ from the defaults</span>
          </div>
        </div>
      )}

      {tab === "ai" && (
        <div className="space-y-5">
          <div className="space-y-2">
            <div className="flex items-center gap-2"><p className="text-sm font-semibold">Cortex model</p><Customised k="LLM_MODEL" /></div>
            <p className="text-xs text-muted-foreground">Used by every AI step (profiling notes, mapping, STTM, quality, QA, reviews). Pick one available in your Snowflake region.</p>
            <div className="flex flex-wrap gap-2">
              <input list="known-models" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Cortex model"
                     className="h-9 w-72 rounded-md border bg-background px-2 font-mono text-sm" />
              <datalist id="known-models">{platform.known_models.map((m) => <option key={m} value={m} />)}</datalist>
              <Button size="sm" disabled={pending} onClick={() => saveSetting("LLM_MODEL", model)}><Save className="h-4 w-4" /> Save</Button>
              <Button size="sm" variant="ghost" disabled={pending} onClick={() => saveSetting("LLM_MODEL", null, true)}><RotateCcw className="h-4 w-4" /> Default</Button>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-2"><p className="text-sm font-semibold">Credits per million tokens</p><Customised k="CREDITS_PER_MILLION_TOKENS" /></div>
            <p className="text-xs text-muted-foreground">Used to estimate cost on the Audit page. Model name to credits; &quot;default&quot; covers any model not listed.</p>
            <textarea value={rates} onChange={(e) => setRates(e.target.value)} rows={5} aria-label="Cost rates"
                      className="w-full max-w-xl rounded-md border bg-background p-2 font-mono text-xs" />
            <div className="flex gap-2">
              <Button size="sm" disabled={pending} onClick={() => { const v = parseJson(rates); if (v !== undefined) saveSetting("CREDITS_PER_MILLION_TOKENS", v); }}>
                <Save className="h-4 w-4" /> Save
              </Button>
              <Button size="sm" variant="ghost" disabled={pending} onClick={() => saveSetting("CREDITS_PER_MILLION_TOKENS", null, true)}><RotateCcw className="h-4 w-4" /> Default</Button>
            </div>
          </div>
        </div>
      )}

      {tab === "catalog" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <p className="text-sm text-muted-foreground">Objects hidden from task screens (seed and proof-of-concept leftovers) and product tokens removed from suggested names. One per line.</p>
            <Customised k="CATALOG_DISPLAY" />
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(CATALOG_LABELS).map(([k, label]) => (
              <label key={k} className="space-y-1 text-xs font-medium">
                {label}
                <textarea value={catalog[k]} onChange={(e) => setCatalog((c) => ({ ...c, [k]: e.target.value }))} rows={3}
                          className="w-full rounded-md border bg-background p-2 font-mono text-xs" />
              </label>
            ))}
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={pending} onClick={() => saveSetting("CATALOG_DISPLAY",
              Object.fromEntries(Object.entries(catalog).map(([k, v]) => [k, v.split("\n").map((x) => x.trim()).filter(Boolean)])))}>
              <Save className="h-4 w-4" /> Save
            </Button>
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => saveSetting("CATALOG_DISPLAY", null, true)}><RotateCcw className="h-4 w-4" /> Default</Button>
          </div>
        </div>
      )}

      {tab === "standards" && (
        <div className="grid gap-4 lg:grid-cols-2">
          {(["GDP", "GENERIC"] as const).map((std) => (
            <div key={std} className="space-y-2 rounded-xl border p-3">
              <div className="flex items-center gap-2">
                <p className="text-sm font-semibold">{std === "GDP" ? "GDP standard" : "Generic standard (non-GDP sources)"}</p>
                <Customised k={`MODELING_STANDARD.${std}`} />
              </div>
              <p className="text-xs text-muted-foreground">Preset (read-only) and your overrides. Only keys from the preset are accepted.</p>
              <pre className="max-h-48 overflow-auto rounded-md bg-muted/40 p-2 text-[11px]">{JSON.stringify(platform.presets[std], null, 2)}</pre>
              <textarea value={overrides[std]} onChange={(e) => setOverrides((o) => ({ ...o, [std]: e.target.value }))} rows={5}
                        aria-label={`${std} overrides`} className="w-full rounded-md border bg-background p-2 font-mono text-xs" />
              <div className="flex gap-2">
                <Button size="sm" disabled={pending} onClick={() => { const v = parseJson(overrides[std]); if (v !== undefined) saveSetting(`MODELING_STANDARD.${std}`, v); }}>
                  <Save className="h-4 w-4" /> Save
                </Button>
                <Button size="sm" variant="ghost" disabled={pending} onClick={() => { setOverrides((o) => ({ ...o, [std]: "{}" })); saveSetting(`MODELING_STANDARD.${std}`, null, true); }}>
                  <RotateCcw className="h-4 w-4" /> Default
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {notice && <p className="text-sm text-success">{notice}</p>}
    </section>
  );
}
