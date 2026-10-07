"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { savePlatformSetting, type PlatformState } from "./actions";
import { DirtyBar, useToast } from "./admin-ui";
import { Panel } from "./section";

type Std = "GDP" | "GENERIC";
const TITLES: Record<Std, [string, string]> = {
  GDP: ["GDP standard", "Naming and modeling conventions for runs on GDP sources."],
  GENERIC: ["Generic standard", "Conventions for runs on any other source."],
};

const show = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

function parse(sample: unknown, text: string): unknown {
  if (typeof sample === "boolean") return text === "true";
  if (typeof sample === "number") return Number(text);
  if (typeof sample === "string") return text;
  return JSON.parse(text);
}

/** Preset conventions with an override column: blank means "use the preset". */
function StandardTable({ std, preset, value, onChange }: {
  std: Std; preset: Record<string, unknown>; value: Record<string, string>; onChange: (k: string, v: string) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
          <tr><th className="px-3 py-2 font-medium">Convention</th><th className="px-3 py-2 font-medium">Preset</th>
            <th className="px-3 py-2 font-medium">Your value</th></tr>
        </thead>
        <tbody className="divide-y">
          {Object.entries(preset).map(([k, p]) => {
            const v = value[k] ?? "";
            return (
              <tr key={k} className={cn(v && "bg-primary/5")}>
                <td className="px-3 py-2 align-top">
                  <p className="capitalize">{k.replace(/_/g, " ")}</p>
                  <p className="font-mono text-[10px] text-muted-foreground">{k}</p>
                </td>
                <td className="max-w-[260px] px-3 py-2 align-top font-mono text-xs text-muted-foreground"><span className="break-all">{show(p)}</span></td>
                <td className="px-3 py-1.5 align-top">
                  <div className="flex items-center gap-1.5">
                    {typeof p === "boolean" ? (
                      <select value={v} onChange={(e) => onChange(k, e.target.value)} aria-label={`${std} ${k}`}
                              className="h-8 rounded-md border bg-background px-2 text-sm">
                        <option value="">Use preset</option><option value="true">On</option><option value="false">Off</option>
                      </select>
                    ) : (
                      <Input value={v} onChange={(e) => onChange(k, e.target.value)} placeholder="Use preset" aria-label={`${std} ${k}`}
                             inputMode={typeof p === "number" ? "decimal" : undefined} className="h-8 font-mono text-xs" />
                    )}
                    {v && (
                      <button type="button" aria-label={`Use preset for ${k}`} title="Use preset" className="text-primary" onClick={() => onChange(k, "")}>
                        <RotateCcw className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const toText = (o: unknown): Record<string, string> =>
  Object.fromEntries(Object.entries((o as Record<string, unknown>) ?? {}).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]));

export function StandardsSection({ platform }: { platform: PlatformState }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const initial = {
    GDP: toText(platform.settings["MODELING_STANDARD.GDP"]?.value),
    GENERIC: toText(platform.settings["MODELING_STANDARD.GENERIC"]?.value),
  };
  const [saved, setSaved] = useState(initial);
  const [draft, setDraft] = useState(initial);
  const clean = (o: Record<string, string>) => JSON.stringify(Object.entries(o).filter(([, v]) => v !== "").sort());
  const changed = (["GDP", "GENERIC"] as Std[]).filter((s) => clean(draft[s]) !== clean(saved[s]));

  const save = () => start(async () => {
    for (const std of changed) {
      const preset = platform.presets[std] ?? {};
      let value: Record<string, unknown>;
      try {
        value = Object.fromEntries(Object.entries(draft[std]).filter(([, v]) => v !== "").map(([k, v]) => [k, parse(preset[k], v)]));
      } catch {
        toast("error", `${TITLES[std][0]}: a list or object value is not valid JSON.`); return;
      }
      const r = await savePlatformSetting(`MODELING_STANDARD.${std}`, value);
      if (!r.ok) { toast("error", r.error); return; }
    }
    setSaved(draft);
    toast("ok", "Modeling standards saved. New runs use them.");
    router.refresh();
  });

  return (
    <div className="space-y-5">
      {(["GDP", "GENERIC"] as Std[]).map((std) => {
        const s = platform.settings[`MODELING_STANDARD.${std}`];
        const count = Object.values(draft[std]).filter(Boolean).length;
        return (
          <Panel key={std}
                 title={<span className="flex items-center gap-2">{TITLES[std][0]}
                   {count ? <Badge variant="outline">{count} override{count === 1 ? "" : "s"}</Badge> : <Badge variant="secondary">preset</Badge>}</span>}
                 description={<>{TITLES[std][1]}{s?.customised && s.changed_by ? ` Last changed by ${s.changed_by}, ${s.changed_at?.slice(0, 16)}.` : ""}</>}
                 actions={count > 0 && (
                   <Button size="sm" variant="ghost" onClick={() => setDraft((d) => ({ ...d, [std]: {} }))}>
                     <RotateCcw className="h-3.5 w-3.5" /> Use preset for all
                   </Button>
                 )}>
            <StandardTable std={std} preset={platform.presets[std] ?? {}} value={draft[std]}
                           onChange={(k, v) => setDraft((d) => ({ ...d, [std]: { ...d[std], [k]: v } }))} />
          </Panel>
        );
      })}
      <DirtyBar count={changed.length} pending={pending} onSave={save} onDiscard={() => setDraft(saved)} what="standard" />
    </div>
  );
}
