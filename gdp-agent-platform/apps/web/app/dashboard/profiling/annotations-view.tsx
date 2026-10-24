"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Check, Loader2, MessageSquareText, Undo2, X } from "lucide-react";
import type { ColumnAnnotation } from "@/lib/hub-types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { EmptyState, StatusPill } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { decideAnnotation } from "./actions";

const LABEL: Record<string, string> = {
  description: "Description", business_definition: "Business definition", pii_classification: "PII class",
  semantic_type: "Semantic type", accepted_values: "Accepted values",
};

export function AnnotationFields({ a }: { a: ColumnAnnotation }) {
  const detected = a.detected as Record<string, string | null>;
  return (
    <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[10rem_1fr]">
      {(Object.keys(LABEL) as (keyof ColumnAnnotation)[]).filter((k) => (k === "accepted_values" ? a.accepted_values.length : a[k])).map((k) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{LABEL[k]}</dt>
          <dd>
            {k === "accepted_values" ? a.accepted_values.join(", ") : String(a[k])}
            {detected?.[k] && detected[k] !== a[k] ? <span className="ml-2 text-muted-foreground">(profiler: {String(detected[k])})</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Column annotations waiting for a data steward, and the recent decisions. */
export function AnnotationsView({ annotations, me, canDecide }: { annotations: ColumnAnnotation[]; me: string; canDecide: boolean }) {
  const toast = useToast();
  const router = useRouter();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [pending, start] = useTransition();
  const open = annotations.filter((a) => a.status === "PROPOSED");
  const done = annotations.filter((a) => a.status !== "PROPOSED").slice(0, 30);
  const decide = (a: ColumnAnnotation, d: "approve" | "reject" | "withdraw") => start(async () => {
    const r = await decideAnnotation(a.annotation_id, d, notes[a.annotation_id]);
    if (!r.ok) { toast("error", r.error); return; }
    const m = r.data.model;
    toast("ok", d === "approve"
      ? `Approved${m?.change_id ? `; proposed to the domain model (${m.entity})` : m?.skipped ? `; model not changed: ${m.skipped}` : ""}`
      : d === "reject" ? "Rejected" : "Withdrawn");
    router.refresh();
  });
  return (
    <div className="space-y-4" data-testid="annotations">
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Waiting for review ({open.length})</h3>
        {!open.length ? <EmptyState icon={MessageSquareText} title="Nothing to review">Annotate a column from a profile (open a table, expand a column, Annotate).</EmptyState>
          : open.map((a) => {
            const own = (a.proposed_by ?? "").toUpperCase() === me;
            return (
              <article key={a.annotation_id} className="surface space-y-2 p-3" data-testid={`annotation-${a.column_name}`}>
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono font-semibold">{a.table_fqn}.{a.column_name}</span>
                  <span className="text-xs text-muted-foreground">proposed by {a.proposed_by} · {a.proposed_at?.slice(0, 16)}</span>
                </p>
                <AnnotationFields a={a} />
                {a.note && <p className="text-xs text-muted-foreground">Why: {a.note}</p>}
                {own ? (
                  <Button size="sm" variant="outline" disabled={pending} onClick={() => decide(a, "withdraw")}><Undo2 className="h-3.5 w-3.5" /> Withdraw</Button>
                ) : canDecide ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <Textarea aria-label="Decision note" rows={1} className="min-h-0 flex-1 text-xs" value={notes[a.annotation_id] ?? ""}
                              onChange={(e) => setNotes({ ...notes, [a.annotation_id]: e.target.value })} placeholder="Note (needed to reject)" />
                    <Button size="sm" disabled={pending} onClick={() => decide(a, "approve")} data-testid={`approve-${a.column_name}`}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Approve</Button>
                    <Button size="sm" variant="outline" className="text-destructive" disabled={pending || (notes[a.annotation_id] ?? "").trim().length < 3} onClick={() => decide(a, "reject")}><X className="h-3.5 w-3.5" /> Reject</Button>
                  </div>
                ) : <p className="text-xs text-muted-foreground">Waiting for a data steward.</p>}
              </article>
            );
          })}
      </section>
      {done.length > 0 && (
        <section className="surface overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
              <tr><th className="px-3 py-2 text-left">Column</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">People</th><th className="px-3 py-2 text-left">Domain model</th></tr>
            </thead>
            <tbody>
              {done.map((a) => (
                <tr key={a.annotation_id} className="border-t border-border/60">
                  <td className="px-3 py-2 font-mono text-xs">{a.table_fqn}.{a.column_name}</td>
                  <td className="px-3 py-2"><StatusPill status={a.status} /></td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{a.proposed_by}{a.decided_by ? ` · ${a.decided_by}` : ""}{a.decision_note ? `: ${a.decision_note}` : ""}</td>
                  <td className="px-3 py-2 text-xs">{a.model_id ? <Link className="text-primary hover:underline" href={`/os/design/modeling/${a.model_id}?tab=changes`}>Proposed to the model</Link> : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
