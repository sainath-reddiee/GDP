"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { savePlatformSetting } from "./actions";
import { DirtyBar, useToast } from "./admin-ui";
import { Panel } from "./section";

type Rates = Record<string, { input?: number; output?: number }>;
type Draft = { model: string; input: string; output: string }[];

const toDraft = (r: Rates): Draft =>
  Object.entries(r).sort(([a], [b]) => a.localeCompare(b))
    .map(([model, v]) => ({ model, input: v.input == null ? "" : String(v.input), output: v.output == null ? "" : String(v.output) }));
const key = (d: Draft) => JSON.stringify(d.filter((r) => r.model.trim()));

/** Credits per million tokens, input and output, per model; plus the fallback rate and the credit price. */
export function RateCardEditor({ rateCard, fallback, legacy, price, modelNames, billed }: {
  rateCard: Rates; fallback: number; legacy: Record<string, number>; price: number | null; modelNames: string[];
  billed: Record<string, number>;
}) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const [saved, setSaved] = useState(() => ({ rows: toDraft(rateCard), fallback: String(fallback || ""), price: price == null ? "" : String(price) }));
  const [rows, setRows] = useState<Draft>(saved.rows);
  const [fb, setFb] = useState(saved.fallback);
  const [usd, setUsd] = useState(saved.price);
  const [adding, setAdding] = useState("");

  const dirty = (key(rows) !== key(saved.rows) ? 1 : 0) + (fb !== saved.fallback ? 1 : 0) + (usd !== saved.price ? 1 : 0);
  const unpriced = useMemo(() => modelNames.filter((m) => !rows.some((r) => r.model === m)).sort(), [modelNames, rows]);

  const save = () => start(async () => {
    if (key(rows) !== key(saved.rows)) {
      const card = Object.fromEntries(rows.filter((r) => r.model.trim()).map((r) => [r.model.trim(), { input: r.input, output: r.output }]));
      const res = await savePlatformSetting("RATE_CARD", card);
      if (!res.ok) { toast("error", res.error); return; }
    }
    if (fb !== saved.fallback) {
      const res = await savePlatformSetting("CREDITS_PER_MILLION_TOKENS", { ...legacy, default: fb === "" ? 0 : Number(fb) });
      if (!res.ok) { toast("error", res.error); return; }
    }
    if (usd !== saved.price) {
      const res = await savePlatformSetting("CREDIT_PRICE_USD", usd === "" ? null : usd);
      if (!res.ok) { toast("error", res.error); return; }
    }
    setSaved({ rows, fallback: fb, price: usd });
    toast("ok", "Rates saved. New calls use them; use Reconcile to re-estimate earlier calls that showed 0.");
    router.refresh();
  });

  const set = (i: number, k: "input" | "output", v: string) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v.replace(/[^0-9.]/g, "") } : r)));

  return (
    <>
      <Panel title="Rate card"
             description="Credits per million tokens for each model, input and output priced separately. Copy the values from Snowflake's service consumption table for your edition. Models without a rate use this account's own billed rate once Snowflake has reported it, then the fallback.">
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr><th className="px-3 py-2 font-medium">Model</th><th className="px-3 py-2 font-medium">Input</th>
                <th className="px-3 py-2 font-medium">Output</th>
                <th className="px-3 py-2 font-medium" title="Credits per million tokens this account was actually billed (blended input and output)">Billed rate</th>
                <th className="w-10" /></tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((r, i) => (
                <tr key={r.model}>
                  <td className="px-3 py-2 font-mono text-xs">{r.model}</td>
                  <td className="px-3 py-1.5"><Input value={r.input} onChange={(e) => set(i, "input", e.target.value)} inputMode="decimal" aria-label={`${r.model} input rate`} className="h-8 w-28 tabular-nums" /></td>
                  <td className="px-3 py-1.5"><Input value={r.output} onChange={(e) => set(i, "output", e.target.value)} inputMode="decimal" aria-label={`${r.model} output rate`} className="h-8 w-28 tabular-nums" /></td>
                  <td className="px-3 py-2 text-xs tabular-nums text-muted-foreground">{billed[r.model] != null ? billed[r.model].toFixed(3) : "-"}</td>
                  <td className="px-2">
                    <button type="button" aria-label={`Remove ${r.model}`} className="text-muted-foreground hover:text-destructive"
                            onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-4 text-center text-xs text-muted-foreground">No rates set. Estimates use the billed rates learned from Snowflake below.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {Object.keys(billed).length > 0 && (
          <p className="mt-3 text-[11px] text-muted-foreground">
            Learned from this account&apos;s Cortex billing (credits per million tokens):{" "}
            {Object.entries(billed).sort(([, a], [, b]) => b - a).map(([m, v]) => `${m} ${v.toFixed(3)}`).join(" · ")}.
            These apply automatically to models without a rate here.
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Model to add"
                  className="h-9 rounded-md border bg-background px-2 text-sm">
            <option value="">Choose a model</option>
            {unpriced.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <Button size="sm" variant="outline" disabled={!adding}
                  onClick={() => { setRows((rs) => [...rs, { model: adding, input: "", output: "" }]); setAdding(""); }}>
            <Plus className="h-3.5 w-3.5" /> Add rate
          </Button>
        </div>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="block text-xs font-medium">
            Fallback rate (credits per million tokens, any model)
            <Input value={fb} onChange={(e) => setFb(e.target.value.replace(/[^0-9.]/g, ""))} inputMode="decimal" placeholder="0" className="mt-1 h-9 w-40" />
          </label>
          <label className="block text-xs font-medium">
            Credit price in USD (optional)
            <Input value={usd} onChange={(e) => setUsd(e.target.value.replace(/[^0-9.]/g, ""))} inputMode="decimal" placeholder="for example 3.00" className="mt-1 h-9 w-40" />
          </label>
        </div>
      </Panel>
      <DirtyBar count={dirty} pending={pending} onSave={save}
                onDiscard={() => { setRows(saved.rows); setFb(saved.fallback); setUsd(saved.price); }} />
    </>
  );
}
