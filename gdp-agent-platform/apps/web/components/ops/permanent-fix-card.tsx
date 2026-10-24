"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { ArrowRight, Hammer, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { PermanentFixStatusPill } from "@/components/ops/status-pill";
import { draftPermanentFix, type PermanentFixRow } from "@/app/ops/permanent-fix-actions";

const OPEN = ["DRAFTED", "ACCEPTED", "PUBLISHED"];

/** On a known fix's page: its newest permanent fix proposal, and a button to ask the agent for one (a published fix
 *  only). A new draft while one is open replaces it, after a confirmation. */
export function PermanentFixCard({ issueId, published, latest, canDraft, runs30d }: {
  issueId: string; published: boolean; latest: PermanentFixRow | null; canDraft: boolean; runs30d?: number | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const replacing = !!latest && OPEN.includes(latest.status);
  const draft = () => start(async () => {
    setError("");
    const r = await draftPermanentFix(issueId, instructions, replacing);
    if (!r.ok) { setError(r.error); return; }
    router.push(`/os/run/ops/permanent-fixes/${encodeURIComponent(r.data.fix_id)}`);
  });

  return (
    <section className="surface space-y-3 p-4" data-testid="permanent-fix-card">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><Hammer className="h-4 w-4 text-primary" />Permanent fix</h2>
      <p className="text-xs text-muted-foreground">
        This fix repairs the failure after it happens{runs30d ? ` (${runs30d} times in 30 days)` : ""}. A permanent fix changes the pipeline&apos;s code so it stops failing.
      </p>
      {latest && (
        <Link href={`/os/run/ops/permanent-fixes/${encodeURIComponent(latest.fix_id)}`} className="flex items-center gap-2 rounded-lg border p-2.5 text-xs transition hover:bg-muted/40">
          <PermanentFixStatusPill status={latest.status} />
          <span className="min-w-0 flex-1 truncate font-medium">{latest.title ?? "Permanent fix"}</span>
          <ArrowRight className="h-3 w-3 text-muted-foreground" />
        </Link>
      )}
      {latest?.status === "MERGED" && <p className="text-xs">The change merged. Open it to decide whether to retire this known fix.</p>}
      {canDraft && published && (open ? (
        <div className="space-y-2">
          <Textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3} maxLength={2000}
                    placeholder="Optional: what the change should do (for example: default missing amounts to zero in the staging model)"
                    aria-label="Instructions for the draft" />
          {replacing && <p className="text-xs text-amber-700">An open proposal exists; the new draft replaces it.</p>}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={pending} onClick={draft}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}{pending ? "Drafting, up to a minute" : "Draft it"}
            </Button>
            <Button size="sm" variant="outline" disabled={pending} onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Sparkles className="h-3.5 w-3.5" />{replacing ? "Redraft permanent fix" : "Draft permanent fix"}
        </Button>
      ))}
      {!published && <p className="text-xs text-muted-foreground">A permanent fix is drafted for a published known fix.</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </section>
  );
}
