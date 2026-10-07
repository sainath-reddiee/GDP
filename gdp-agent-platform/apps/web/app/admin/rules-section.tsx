"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { RotateCcw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { saveRules, type RulesState } from "./actions";
import { DirtyBar, useToast } from "./admin-ui";
import { Panel } from "./section";

const GROUPS: [string, string, string][] = [
  ["quality.", "Data quality checks", "Thresholds the generated checks use."],
  ["profile.", "Profiling", "Sampling and what counts as a key, a placeholder or PII."],
  ["relationships.", "Relationships", "How joins between tables are proposed."],
  ["joins.", "Joins", "Join validation limits."],
  ["mapping.", "Mapping", "Confidence needed before a mapping is proposed."],
  ["domain.", "Domain detection", "How a run's domain is recognised."],
  ["ui.", "Confidence bands", "Where pages draw the high, medium and low lines."],
  ["hints.", "Name hints", "Second-line evidence after the values."],
];

const toText = (v: unknown) => (Array.isArray(v) ? v.join(", ") : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? ""));

function parseLike(sample: unknown, text: string): unknown {
  if (typeof sample === "number") return text.trim() === "" ? sample : Number(text);
  if (typeof sample === "boolean") return text.trim().toLowerCase() === "true";
  if (Array.isArray(sample)) return text.split(",").map((s) => s.trim()).filter(Boolean);
  if (typeof sample === "object" && sample !== null) return JSON.parse(text || "{}");
  return text;
}

const label = (k: string) => k.split(".").slice(1).join(" ").replace(/_/g, " ");

/** Platform-wide rules and thresholds, grouped, searchable, with per-value reset and one save. */
export function RulesSection({ rules }: { rules: RulesState }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const initial = useMemo(() => Object.fromEntries(Object.entries(rules.rules).map(([k, v]) => [k, toText(v)])), [rules.rules]);
  const [saved, setSaved] = useState<Record<string, string>>(initial);
  const [text, setText] = useState<Record<string, string>>(initial);
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);

  const keys = Object.keys(rules.defaults);
  const dirty = keys.filter((k) => (text[k] ?? "") !== (saved[k] ?? "")).length;
  const custom = keys.filter((k) => (text[k] ?? "") !== toText(rules.defaults[k]));
  const match = (k: string) => (!query || k.toLowerCase().includes(query.toLowerCase()))
    && (!onlyChanged || custom.includes(k));

  const save = () => start(async () => {
    const out: Record<string, unknown> = {};
    try {
      for (const k of custom) out[k] = parseLike(rules.defaults[k], text[k]);
    } catch {
      toast("error", "A value is not valid JSON."); return;
    }
    const r = await saveRules(out);
    if (!r.ok) { toast("error", r.error); return; }
    setSaved(text);
    toast("ok", `Saved. ${r.data.overridden.length} rule(s) differ from the defaults; stages pick them up within a minute.`);
    router.refresh();
  });

  const other = keys.filter((k) => !GROUPS.some(([p]) => k.startsWith(p)));
  const groups: [string, string, string, string[]][] = [
    ...GROUPS.map(([p, t, d]) => [p, t, d, keys.filter((k) => k.startsWith(p))] as [string, string, string, string[]]),
    ["other", "Other", "", other],
  ];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search rules" className="pl-8" aria-label="Search rules" />
        </div>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} />
          Only values that differ from the default ({custom.length})
        </label>
      </div>
      <p className="text-xs text-muted-foreground">Platform-wide values; a domain can override them again for its own runs. Lists are comma-separated.</p>
      {groups.map(([prefix, title, description, all]) => {
        const shown = all.filter(match);
        if (!shown.length) return null;
        const changed = all.filter((k) => custom.includes(k)).length;
        return (
          <Panel key={prefix} title={<span className="flex items-center gap-2">{title}{changed > 0 && <Badge variant="outline">{changed} changed</Badge>}</span>}
                 description={description}>
            <div className="-my-2 divide-y">
              {shown.map((k) => {
                const isDirty = (text[k] ?? "") !== (saved[k] ?? "");
                const isCustom = custom.includes(k);
                const def = toText(rules.defaults[k]);
                const isBool = typeof rules.defaults[k] === "boolean";
                return (
                  <div key={k} className="grid items-center gap-2 py-2.5 sm:grid-cols-[minmax(180px,280px)_1fr_auto]">
                    <div className="min-w-0">
                      <p className="text-sm capitalize">{label(k)}</p>
                      <p className="truncate font-mono text-[10px] text-muted-foreground">{k}</p>
                    </div>
                    {isBool ? (
                      <select value={text[k] ?? ""} onChange={(e) => setText((t) => ({ ...t, [k]: e.target.value }))} aria-label={k}
                              className={cn("h-8 w-32 rounded-md border bg-background px-2 text-sm", isDirty && "border-primary ring-2 ring-primary/20")}>
                        <option value="true">On</option><option value="false">Off</option>
                      </select>
                    ) : (
                      <Input value={text[k] ?? ""} onChange={(e) => setText((t) => ({ ...t, [k]: e.target.value }))} aria-label={k}
                             className={cn("h-8 font-mono text-xs", isDirty && "border-primary ring-2 ring-primary/20")} />
                    )}
                    <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span className="max-w-[200px] truncate" title={def}>default {def || "none"}</span>
                      {isCustom && (
                        <button type="button" title="Back to the default" aria-label={`Reset ${k}`} className="text-primary"
                                onClick={() => setText((t) => ({ ...t, [k]: def }))}>
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </Panel>
        );
      })}
      <DirtyBar count={dirty} pending={pending} onSave={save} onDiscard={() => setText(saved)} what="rule" />
    </div>
  );
}
