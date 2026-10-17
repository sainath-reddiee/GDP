"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Archive, Check, Eye, GitPullRequest, Loader2, ShieldCheck, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { UnifiedDiff } from "@/app/qa/cases/[id]/case-kit";
import {
  decideKnownFix, decidePermanentFix, previewPermanentFix, publishPermanentFix, type PermanentFix, type PublishPreview,
} from "../../permanent-fix-actions";

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

/** What a person can do with a permanent fix now: accept or reject a draft (never the one who drafted it), open the
 *  pull request after a dry run, and after the merge retire or keep the known fix. */
export function PermanentFixReview({ fix, canDecide, canPublish }: { fix: PermanentFix; canDecide: boolean; canPublish: boolean }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [preview, setPreview] = useState<PublishPreview | null>(null);
  const [pending, start] = useTransition();
  const run = <T,>(fn: () => Promise<Result<T>>, after: (d: T) => string) => start(async () => {
    setError(""); setDone("");
    const r = await fn();
    if (!r.ok) { setError(r.error); return; }
    setDone(after(r.data));
    router.refresh();
  });
  const spin = <Loader2 className="h-3.5 w-3.5 animate-spin" />;

  return (
    <section className="surface space-y-3 p-4" data-testid="permanent-fix-review">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="h-4 w-4 text-human" />Review</h2>

      {fix.status === "DRAFTED" && (!canDecide
        ? <p className="text-xs text-muted-foreground">Accepting a permanent fix needs the OPS.FIX_PUBLISH privilege.</p>
        : fix.you_drafted
          ? <p className="text-xs text-muted-foreground">You asked for this draft, so a second person accepts or rejects it.</p>
          : (
            <div className="space-y-2">
              <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000}
                        placeholder="Note (needed to reject: say what is wrong)" aria-label="Decision note" />
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={pending} onClick={() => run(() => decidePermanentFix(fix.fix_id, true, note), () => "Accepted. Open the pull request when you are ready.")}>
                  {pending ? spin : <Check className="h-3.5 w-3.5" />}Accept
                </Button>
                <Button size="sm" variant="outline" disabled={pending || !note.trim()} title={note.trim() ? undefined : "Say why first"}
                        onClick={() => run(() => decidePermanentFix(fix.fix_id, false, note), () => "Rejected. The agent drafts again only if the fix keeps running.")}>
                  <X className="h-3.5 w-3.5" />Reject
                </Button>
              </div>
            </div>
          ))}

      {fix.status === "ACCEPTED" && (!canPublish
        ? <p className="text-xs text-muted-foreground">Opening the pull request needs the DBT.EDIT privilege.</p>
        : !fix.publishable
          ? <p className="text-xs text-muted-foreground">{fix.unavailable_reason ?? "This change cannot be published from the platform."}</p>
          : preview ? (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                A draft pull request from <code className="rounded bg-muted px-1">{preview.branch}</code> into <code className="rounded bg-muted px-1">{preview.base_branch}</code> of {preview.repo},
                changing {preview.files.length} file{preview.files.length === 1 ? "" : "s"}. The preview is valid for 10 minutes.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={pending} onClick={() => run(() => publishPermanentFix(fix.fix_id, preview.preview_token), (d) => {
                  setPreview(null);
                  return d.result.status === "PUBLISHED" ? "Draft pull request opened." : `Not opened: ${d.result.detail ?? d.result.status}`;
                })}>{pending ? spin : <GitPullRequest className="h-3.5 w-3.5" />}Open draft pull request</Button>
                <Button size="sm" variant="outline" disabled={pending} onClick={() => setPreview(null)}>Cancel</Button>
              </div>
            </div>
          ) : (
            <Button size="sm" variant="outline" disabled={pending} onClick={() => run(() => previewPermanentFix(fix.fix_id), (p) => { setPreview(p); return ""; })}>
              {pending ? spin : <Eye className="h-3.5 w-3.5" />}Preview the pull request
            </Button>
          ))}

      {fix.status === "PUBLISHED" && <p className="text-xs text-muted-foreground">The pull request is with the repository&apos;s reviewers. Its state is read back every 30 minutes.</p>}

      {fix.status === "MERGED" && (fix.retire_decision ? (
        <p className="text-xs text-muted-foreground">
          The known fix was {fix.retire_decision === "RETIRED" ? "retired" : "kept"} by {fix.retire_decided_by}.
        </p>
      ) : canDecide ? (
        <div className="space-y-2">
          <p className="text-xs">The change merged. Retire the known fix now that the cause is gone, or keep it as a safety net?</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={pending} onClick={() => run(() => decideKnownFix(fix.fix_id, true), () => "The known fix is retired.")}>
              {pending ? spin : <Archive className="h-3.5 w-3.5" />}Retire the known fix
            </Button>
            <Button size="sm" variant="outline" disabled={pending} onClick={() => run(() => decideKnownFix(fix.fix_id, false), () => "The known fix stays published.")}>Keep it</Button>
          </div>
        </div>
      ) : <p className="text-xs text-muted-foreground">The change merged; a fix publisher decides whether to retire the known fix.</p>)}

      {["REJECTED", "CLOSED", "SUPERSEDED", "UNAVAILABLE"].includes(fix.status) && (
        <p className="text-xs text-muted-foreground">Nothing to decide. Ask for a new draft from the known fix&apos;s page if the failure keeps coming back.</p>
      )}

      {preview?.diff && <UnifiedDiff diff={preview.diff} />}
      {done && <p role="status" className="text-xs text-success">{done}</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </section>
  );
}
