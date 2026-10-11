"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";
import { AlertTriangle, Check, CheckCircle2, Loader2, MessageSquare, Radar, ShieldAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChangeClass, DmComment, DmDiff, DmImpact, DmVersion } from "@/lib/model-types";
import { Button } from "@/components/ui/button";
import { Label, Select, Textarea } from "@/components/ui/input";
import { explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { addComment, applyVersion, approveVersion, loadComments, loadDiff, loadImpact, rejectVersion, resolveComment } from "../model-actions";

export const CLASS_STYLE: Record<ChangeClass, string> = {
  BREAKING: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  ADDITIVE: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  NON_FUNCTIONAL: "bg-slate-100 text-slate-700 dark:bg-slate-500/15 dark:text-slate-300",
};
const CLASS_LABEL: Record<ChangeClass, string> = { BREAKING: "breaking", ADDITIVE: "additive", NON_FUNCTIONAL: "documentation" };
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "none" : typeof v === "object" ? JSON.stringify(v) : String(v));

export function DiffList({ diff }: { diff: DmDiff }) {
  const groups = useMemo(() => {
    const out = new Map<string, DmDiff["changes"]>();
    for (const c of diff.changes) out.set(c.entity || "Model", [...(out.get(c.entity || "Model") ?? []), c]);
    return [...out.entries()];
  }, [diff]);
  if (!diff.changes.length) return <p className="text-sm text-muted-foreground">No changes.</p>;
  return (
    <div className="space-y-3" data-testid="model-diff">
      <p className="flex flex-wrap gap-2 text-xs">
        {(["BREAKING", "ADDITIVE", "NON_FUNCTIONAL"] as ChangeClass[]).map((c) => (
          <span key={c} className={cn("rounded-full px-2 py-0.5 font-semibold", CLASS_STYLE[c])}>{diff.counts[c]} {CLASS_LABEL[c]}</span>
        ))}
      </p>
      {groups.map(([entity, changes]) => (
        <div key={entity} className="rounded-xl border border-border/80">
          <p className="border-b px-3 py-1.5 font-mono text-xs font-semibold">{entity}</p>
          <ul className="divide-y divide-border/60">
            {changes.map((c, i) => (
              <li key={i} className="flex flex-wrap items-start gap-2 px-3 py-1.5 text-xs">
                <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase", CLASS_STYLE[c.class])}>{CLASS_LABEL[c.class]}</span>
                <span className="flex-1">{c.message}</span>
                {(c.before !== undefined || c.after !== undefined) && c.kind.endsWith("_CHANGED") && (
                  <span className="font-mono text-[11px] text-muted-foreground"><span className="line-through">{show(c.before).slice(0, 80)}</span> → {show(c.after).slice(0, 80)}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function ImpactView({ impact }: { impact: DmImpact }) {
  const touched = impact.items.filter((i) => i.total > 0);
  return (
    <div className="space-y-2 text-xs" data-testid="model-impact">
      {!impact.items.length ? <p className="text-muted-foreground">No breaking changes, so nothing downstream is affected.</p>
        : !touched.length ? <p className="text-muted-foreground">No STTM line, check, test, dbt file or open run uses what this version breaks.</p>
        : touched.map((i, n) => (
          <div key={n} className="rounded-lg border border-rose-200 p-2 dark:border-rose-500/30">
            <p className="font-mono font-semibold">{i.entity}{i.column ? `.${i.column}` : ""} <span className="font-sans font-normal text-muted-foreground">({i.kind.toLowerCase().replace(/_/g, " ")})</span></p>
            <ul className="mt-1 space-y-0.5">
              {Object.entries(i.refs).map(([k, rows]) => (
                <li key={k}><span className="font-medium">{impact.labels[k] ?? k}:</span> {rows.length}{rows.length >= 50 ? "+" : ""}
                  <span className="text-muted-foreground"> {rows.slice(0, 4).map((r) => String(r.detail ?? r.id ?? r.run_id ?? "")).filter(Boolean).join(", ")}</span></li>
              ))}
            </ul>
          </div>
        ))}
      {impact.unavailable.length > 0 && <p className="text-muted-foreground">Not checked (not readable here): {impact.unavailable.map((u) => impact.labels[u] ?? u).join(", ")}</p>}
    </div>
  );
}

export function ReviewPanel({ modelId, version, approvedVersion, comments: initial, me, canComment, canDecide, own, dirty }: {
  modelId: string; version: DmVersion; approvedVersion: number | null; comments: DmComment[]; me: string; canComment: boolean;
  canDecide: boolean; own: boolean; dirty: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [diff, setDiff] = useState<DmDiff | null>(null);
  const [impact, setImpact] = useState<DmImpact | null>(null);
  const [comments, setComments] = useState(initial);
  const [path, setPath] = useState("model");
  const [kind, setKind] = useState<DmComment["kind"]>("COMMENT");
  const [body, setBody] = useState("");
  const [ack, setAck] = useState(false);
  const [reason, setReason] = useState("");
  const [pending, start] = useTransition();
  const base = version.version === approvedVersion ? (version.base_version ?? null) : approvedVersion;

  useEffect(() => {
    let live = true;
    (async () => {
      const d = await loadDiff(modelId, version.version, base);
      if (live) setDiff(d.ok ? d.data : null);
      if (d.ok && d.data.breaking) {
        const i = await loadImpact(modelId, version.version, base);
        if (live && i.ok) setImpact(i.data);
      }
    })();
    return () => { live = false; };
  }, [modelId, version.version, base]);

  const paths = useMemo(() => ["model", ...Array.from(new Set((diff?.changes ?? []).map((c) => c.column ? `${c.entity}.${c.column}` : c.entity).filter(Boolean)))], [diff]);
  const threads = useMemo(() => {
    const out = new Map<string, DmComment[]>();
    for (const c of comments) out.set(c.path, [...(out.get(c.path) ?? []), c]);
    return [...out.entries()];
  }, [comments]);
  const blockers = comments.filter((c) => c.kind === "BLOCKER" && !c.resolved).length;

  const reload = async () => { const r = await loadComments(modelId, version.version); if (r.ok) setComments(r.data.comments); };
  const post = () => start(async () => {
    const r = await addComment(modelId, version.version, { body, path, kind });
    if (r.ok) { setBody(""); await reload(); } else toast("error", explain(r.error).message);
  });
  const resolve = (c: DmComment) => start(async () => {
    const r = await resolveComment(modelId, c.comment_id, !c.resolved);
    if (r.ok) await reload(); else toast("error", explain(r.error).message);
  });
  const approve = () => start(async () => {
    const r = await approveVersion(modelId, version.version, ack);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    toast(r.data.registry.applied ? "ok" : "error", r.data.registry.applied
      ? `v${version.version} approved and applied to the domain registry`
      : `v${version.version} approved, but the registry update failed: ${r.data.registry.error}. Apply it again from here.`);
    router.refresh();
  });
  const reject = () => start(async () => {
    const r = await rejectVersion(modelId, version.version, reason);
    if (r.ok) { toast("ok", `v${version.version} rejected`); router.refresh(); } else toast("error", explain(r.error).message);
  });
  const apply = () => start(async () => {
    const r = await applyVersion(modelId, version.version);
    if (r.ok) { toast("ok", "Applied to the domain registry"); router.refresh(); } else toast("error", explain(r.error).message);
  });

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-4">
        <section className="surface space-y-2 p-4">
          <h3 className="text-sm font-semibold">Changes in v{version.version}{base ? ` since approved v${base}` : " (first version)"}</h3>
          {version.change_note && <p className="text-sm text-muted-foreground">{version.change_note}</p>}
          {dirty && <p className="text-xs text-amber-700">You have unsaved edits; the review shows the saved draft.</p>}
          {!diff ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Comparing versions...</p> : <DiffList diff={diff} />}
        </section>
        {diff?.breaking && (
          <section className="surface space-y-2 p-4">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Radar className="h-4 w-4 text-rose-600" /> Impact of the breaking changes</h3>
            {!impact ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Looking for STTM lines, checks, tests, dbt files and runs...</p> : <ImpactView impact={impact} />}
          </section>
        )}
      </div>
      <aside className="space-y-4">
        {version.status === "IN_REVIEW" && (
          <section className="surface space-y-3 p-4" data-testid="decision">
            <h3 className="text-sm font-semibold">Decision</h3>
            {own ? <p className="text-sm text-muted-foreground">You submitted or edited this version, so another model approver decides it (four eyes).</p>
              : !canDecide ? <p className="text-sm text-muted-foreground">A model approver (MODEL.APPROVE) decides this version.</p> : (
              <>
                {blockers > 0 && <p className="flex items-center gap-1.5 text-sm text-destructive"><ShieldAlert className="h-4 w-4" /> {blockers} blocking comment{blockers === 1 ? "" : "s"} must be resolved first.</p>}
                {diff?.breaking && (
                  <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="ack-breaking" />
                    <span>I reviewed the impact of the {diff.counts.BREAKING} breaking change{diff.counts.BREAKING === 1 ? "" : "s"}; dependent mappings, checks and dbt must follow.</span></label>
                )}
                <Button className="w-full" onClick={approve} disabled={pending || blockers > 0 || (!!diff?.breaking && !ack)} data-testid="approve-version">
                  {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Approve and apply</Button>
                <div className="border-t pt-3">
                  <Label htmlFor="reject-why">Reject with a reason</Label>
                  <Textarea id="reject-why" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What needs to change" />
                  <Button variant="outline" className="mt-2 w-full" onClick={reject} disabled={pending || reason.trim().length < 3}><X className="h-4 w-4" /> Reject</Button>
                </div>
              </>
            )}
          </section>
        )}
        {version.status === "APPROVED" && (
          <section className="surface space-y-2 p-4 text-sm">
            <p className="flex items-center gap-1.5 font-medium"><CheckCircle2 className="h-4 w-4 text-success" /> Approved{version.approved_by ? ` by ${version.approved_by}` : ""}{version.approved_at ? ` on ${version.approved_at.slice(0, 10)}` : ""}</p>
            {version.registry_synced_at ? <p className="text-xs text-muted-foreground">Applied to the domain registry {version.registry_synced_at.slice(0, 16)}.</p>
              : canDecide || canComment ? <Button size="sm" variant="outline" onClick={apply} disabled={pending}><AlertTriangle className="h-3.5 w-3.5" /> Apply to the registry</Button> : null}
          </section>
        )}
        {version.status === "REJECTED" && (
          <section className="surface p-4 text-sm"><p className="font-medium text-destructive">Rejected{version.rejected_by ? ` by ${version.rejected_by}` : ""}</p>
            <p className="text-muted-foreground">{version.reject_reason}</p></section>
        )}
        <section className="surface space-y-3 p-4" data-testid="comments">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold"><MessageSquare className="h-4 w-4" /> Comments</h3>
          {!threads.length && <p className="text-xs text-muted-foreground">No comments on this version yet.</p>}
          {threads.map(([p, list]) => (
            <div key={p} className="space-y-1">
              <p className="font-mono text-[11px] font-semibold text-muted-foreground">{p}</p>
              {list.map((c) => (
                <div key={c.comment_id} className={cn("rounded-lg border p-2 text-xs", c.kind === "BLOCKER" && !c.resolved ? "border-rose-300 bg-rose-50 dark:border-rose-500/40 dark:bg-rose-500/10" : "border-border/80", c.resolved && "opacity-60")}>
                  <p className="flex items-center gap-1.5"><span className="font-medium">{c.author}</span>
                    {c.kind !== "COMMENT" && <span className={cn("rounded px-1 text-[10px] font-semibold uppercase", c.kind === "BLOCKER" ? "bg-rose-100 text-rose-700" : "bg-sky-100 text-sky-700")}>{c.kind.toLowerCase()}</span>}
                    <span className="text-muted-foreground">{c.created_at?.slice(0, 16)}</span>
                    {canComment && <button type="button" onClick={() => resolve(c)} className="ml-auto text-[11px] font-medium text-primary hover:underline">{c.resolved ? "Reopen" : "Resolve"}</button>}
                  </p>
                  <p className="mt-0.5 whitespace-pre-wrap">{c.body}</p>
                  {c.resolved && <p className="text-[10px] text-muted-foreground">resolved by {c.resolved_by}</p>}
                </div>
              ))}
            </div>
          ))}
          {canComment && (
            <div className="space-y-2 border-t pt-3">
              <div className="flex gap-1">
                <Select aria-label="Comment on" className="h-8 flex-1 font-mono text-xs" value={path} onChange={(e) => setPath(e.target.value)}>
                  {paths.map((p) => <option key={p}>{p}</option>)}</Select>
                <Select aria-label="Comment kind" className="h-8 w-32 text-xs" value={kind} onChange={(e) => setKind(e.target.value as DmComment["kind"])}>
                  <option value="COMMENT">comment</option><option value="SUGGESTION">suggestion</option><option value="BLOCKER">blocker</option></Select>
              </div>
              <Textarea aria-label="Comment" rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder={`Comment as ${me.toLowerCase()}`} />
              <Button size="sm" onClick={post} disabled={pending || !body.trim()} data-testid="post-comment">Post</Button>
            </div>
          )}
        </section>
      </aside>
    </div>
  );
}
