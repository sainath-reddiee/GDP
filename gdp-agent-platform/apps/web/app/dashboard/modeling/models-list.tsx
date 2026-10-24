"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { GitFork, Layers3, Loader2, Plus, Search, Sparkles } from "lucide-react";
import type { DmListRow } from "@/lib/model-types";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { baselineAll, createModel, forkModel } from "./model-actions";
import { LAYER_META } from "@/lib/layers";
import { cn } from "@/lib/utils";

/** Every domain and the state of its canonical model: approved version, open draft or review, pull request. */
export function ModelsList({ rows, canEdit, layer = "SILVER" }: { rows: DmListRow[]; canEdit: boolean; layer?: "SILVER" | "GOLD" }) {
  const gold = layer === "GOLD";
  const router = useRouter();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [fork, setFork] = useState<DmListRow | null>(null);
  const [name, setName] = useState("");
  const [pending, start] = useTransition();
  const shown = useMemo(() => rows.filter((r) => !q || `${r.domain_name} ${r.description ?? ""}`.toLowerCase().includes(q.toLowerCase())), [rows, q]);
  const missing = gold ? [] : rows.filter((r) => !r.model_id);

  const create = (r: DmListRow) => start(async () => {
    const res = await createModel(r.domain_id);
    if (res.ok) { toast("ok", `Model for ${r.domain_name} created as a draft`); router.push(`/os/design/modeling/${res.data.model_id}`); }
    else toast("error", explain(res.error).message);
  });
  const baseline = () => start(async () => {
    const res = await baselineAll();
    if (res.ok) { toast("ok", res.data.log.length ? `${res.data.log.length} baseline(s) written` : "Every domain with tables already has a model"); router.refresh(); }
    else toast("error", explain(res.error).message);
  });
  const doFork = () => start(async () => {
    if (!fork?.model_id) return;
    const res = await forkModel(fork.model_id, name.trim().toUpperCase());
    if (res.ok) { toast("ok", `${res.data.domain} forked from ${fork.domain_name}`); router.push(`/os/design/modeling/${res.data.model_id}`); }
    else toast("error", explain(res.error).message);
  });

  return (
    <div className="space-y-3" data-testid="domain-models">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border p-0.5 text-xs" role="group" aria-label="Layer">
          {(["SILVER", "GOLD"] as const).map((l) => (
            <Link key={l} href={`/os/design/modeling?layer=${l}`} aria-pressed={layer === l}
                  className={cn("rounded-md px-2.5 py-1", layer === l ? LAYER_META[l].solid : "text-muted-foreground hover:bg-muted")}>
              {LAYER_META[l].label} models</Link>
          ))}
        </div>
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter domains" className="pl-9" aria-label="Filter domains" />
        </div>
        {canEdit && missing.length > 0 && (
          <Button variant="outline" onClick={baseline} disabled={pending}><Sparkles className="h-4 w-4" /> Baseline from registry ({missing.length})</Button>
        )}
      </div>
      {!shown.length ? <EmptyState icon={Layers3} title="No domains yet">Create or import a domain on the Onboarding page first.</EmptyState> : (
        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((r) => (
            <article key={r.domain_id} className="surface flex flex-col gap-2 p-4" data-testid={`model-${r.domain_name}`}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-sm font-semibold">{r.domain_name}</p>
                  <p className="line-clamp-2 text-xs text-muted-foreground">{r.description || "No description"}</p>
                </div>
                {r.open_status ? <StatusPill status={r.open_status} /> : r.approved_version ? <StatusPill status="APPROVED" /> : null}
              </div>
              {r.model_id ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    {r.approved_version ? <>Approved v{r.approved_version}{r.approved_by ? ` by ${r.approved_by}` : ""}</> : "No approved version yet"}
                    {r.open_version ? <> · v{r.open_version} {r.open_status === "IN_REVIEW" ? `in review${r.submitted_by ? ` (${r.submitted_by})` : ""}` : "draft"}</> : null}
                  </p>
                  {r.counts && <p className="text-xs tabular-nums">{r.counts.entities} entities · {r.counts.columns} columns · {r.counts.relationships} relationships</p>}
                  <div className="mt-auto flex items-center gap-2 pt-1">
                    <Link href={`/os/design/modeling/${r.model_id}`} className="text-sm font-medium text-primary hover:underline">Open model</Link>
                    {canEdit && r.approved_version && (
                      <button type="button" onClick={() => { setFork(r); setName(""); }} className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                        <GitFork className="h-3.5 w-3.5" /> Fork</button>
                    )}
                  </div>
                </>
              ) : (
                <div className="mt-auto flex items-center gap-2 pt-1">
                  <span className="text-xs text-muted-foreground">No model yet</span>
                  {canEdit && !gold && <Button size="sm" variant="outline" className="ml-auto" onClick={() => create(r)} disabled={pending}><Plus className="h-3.5 w-3.5" /> Create model</Button>}
                  {gold && <span className="ml-auto text-[11px] text-muted-foreground">Proposed by a Gold run</span>}
                </div>
              )}
            </article>
          ))}
        </section>
      )}
      {fork && (
        <Modal open onClose={() => setFork(null)} title={`Fork ${fork.domain_name}`}
               description={`A new domain whose first draft copies ${fork.domain_name} v${fork.approved_version}. The fork has its own tables and never changes ${fork.domain_name}.`}
               footer={<><Button variant="ghost" onClick={() => setFork(null)}>Cancel</Button>
                 <Button onClick={doFork} disabled={pending || !/^[A-Za-z][A-Za-z0-9_]{1,63}$/.test(name.trim())}>{pending && <Loader2 className="h-4 w-4 animate-spin" />} Fork</Button></>}>
          <Label htmlFor="fork-name">New domain name</Label>
          <Input id="fork-name" className="font-mono" value={name} onChange={(e) => setName(e.target.value.toUpperCase())} placeholder="SALES_EU" />
        </Modal>
      )}
    </div>
  );
}
