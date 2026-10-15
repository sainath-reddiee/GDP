"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";
import { Archive, ArchiveRestore, FileClock, Loader2, MoreHorizontal, XCircle } from "lucide-react";
import { setRunsArchived } from "@/app/runs/actions";
import { transitionRun } from "@/app/runs/[runId]/actions";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** The run's secondary actions in one menu: the audit trail, archive or restore, and cancelling the run. Cancelling
 *  is offered only when the state machine allows it, and asks to confirm first; it cannot be undone. */
export function RunMenu({ runId, archived, canCancel }: { runId: string; archived: boolean; canCancel: boolean }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  const archive = () => start(async () => {
    setError("");
    const r = await setRunsArchived([runId], !archived);
    if (!r.ok) setError(r.error);
    else if (r.data.skipped.length) setError(r.data.skipped[0].reason);
    setOpen(false);
  });
  const cancel = () => start(async () => {
    setError("");
    const r = await transitionRun(runId, "CANCELLED");
    if (!r.ok) { setError(r.error); return; }
    setConfirm(false);
    setOpen(false);
  });

  const item = "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-muted disabled:opacity-50";
  return (
    <div ref={root} className="relative">
      <Button variant="outline" size="sm" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} data-testid="run-menu">
        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MoreHorizontal className="h-4 w-4" />}More
      </Button>
      {open && (
        <div role="menu" className="popover absolute right-0 z-30 mt-1.5 w-60 animate-pop-in p-1.5">
          <Link href={`/runs/${runId}/audit`} role="menuitem" className={item} onClick={() => setOpen(false)}>
            <FileClock className="h-4 w-4 text-muted-foreground" />Audit trail
          </Link>
          <button type="button" role="menuitem" className={item} disabled={pending} onClick={archive}>
            {archived ? <ArchiveRestore className="h-4 w-4 text-muted-foreground" /> : <Archive className="h-4 w-4 text-muted-foreground" />}
            {archived ? "Restore run" : "Archive run"}
          </button>
          {canCancel && (
            <>
              <div className="my-1 border-t" />
              <button type="button" role="menuitem" className={cn(item, "text-destructive")} onClick={() => { setConfirm(true); setOpen(false); }}>
                <XCircle className="h-4 w-4" />Cancel run…
              </button>
            </>
          )}
        </div>
      )}
      {confirm && (
        <div role="alertdialog" aria-label="Confirm cancelling the run"
             className="popover absolute right-0 z-30 mt-1.5 w-80 animate-pop-in space-y-3 p-4">
          <p className="text-sm font-semibold">Cancel this run?</p>
          <p className="text-xs text-muted-foreground">The run stops where it is and cannot be resumed. Its artifacts and audit trail are kept.</p>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(false)}>Keep running</Button>
            <Button size="sm" variant="destructive" disabled={pending} onClick={cancel}>
              {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Cancel run
            </Button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="absolute right-0 mt-1 w-72 text-right text-xs text-destructive">{error}</p>}
    </div>
  );
}
