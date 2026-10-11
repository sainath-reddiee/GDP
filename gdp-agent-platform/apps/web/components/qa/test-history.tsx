"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { History, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/hub/overlay";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { loadTestVersions, restoreTestVersion, type TestVersion } from "@/app/qa/actions";

const CHANGE: Record<TestVersion["change"], string> = {
  CREATED: "created", EDITED: "edited", DELETED: "deleted", RESTORED: "restored", BASELINE: "as it was when history started",
};

function SqlDiff({ lines }: { lines: string[] }) {
  const body = lines.filter((l) => !l.startsWith("---") && !l.startsWith("+++"));
  if (!body.length) return null;
  return (
    <pre className="max-h-48 overflow-auto rounded-lg bg-slate-950 p-2 font-mono text-[11px] leading-5">
      {body.map((l, i) => (
        <span key={i} className={cn("block", l.startsWith("+") ? "text-emerald-300" : l.startsWith("-") ? "text-rose-300" : l.startsWith("@@") ? "text-sky-300" : "text-slate-300")}>{l || " "}</span>
      ))}
    </pre>
  );
}

/** A saved test's history: every create, edit, delete and restore, what changed, and putting an older version back. */
export function TestHistoryButton({ testId, title, canRestore }: { testId: string; title: string; canRestore: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<TestVersion[] | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, start] = useTransition();
  const toast = useToast();
  const show = () => start(async () => {
    setOpen(true); setMessage(null);
    const r = await loadTestVersions(testId);
    setVersions(r.ok ? r.data.versions : []);
    if (!r.ok) setMessage({ ok: false, text: r.error });
  });
  const restore = (v: number) => start(async () => {
    const r = await restoreTestVersion(testId, v);
    if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
    // the workspace refreshes and can remount this card, so the outcome is a toast
    toast("ok", `Version ${v} restored${r.data.note ? ` (${r.data.note})` : ""}.`);
    const again = await loadTestVersions(testId);
    if (again.ok) setVersions(again.data.versions);
    router.refresh();
  });
  return (
    <>
      <button type="button" onClick={show} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" data-testid={`history-${testId}`}>
        <History className="h-3.5 w-3.5" /> History
      </button>
      <Modal open={open} onClose={() => setOpen(false)} wide title={`History of ${title}`}
             description="Every change to this saved test. Restoring checks the old SQL again as if it were typed now.">
        {message && <p role={message.ok ? "status" : "alert"} className={cn("mb-3 text-sm", message.ok ? "text-emerald-700 dark:text-emerald-400" : "text-destructive")}>{message.text}</p>}
        {!versions ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Loading history</p>
          : !versions.length ? <p className="text-sm text-muted-foreground">No history recorded for this test yet.</p> : (
          <ol className="space-y-3" data-testid="test-history">
            {versions.map((v, i) => (
              <li key={v.version} className="rounded-xl border border-border/80 p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-semibold">v{v.version}</span>
                  <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase", v.change === "DELETED" ? "bg-rose-100 text-rose-700" : v.change === "RESTORED" ? "bg-violet-100 text-violet-700" : "bg-muted text-muted-foreground")}>{CHANGE[v.change]}</span>
                  <span className="text-xs text-muted-foreground">{v.changed_by} · {v.changed_at?.slice(0, 16)}</span>
                  {i > 0 && canRestore && !v.is_deleted && (
                    <Button size="sm" variant="outline" className="ml-auto" disabled={pending} onClick={() => restore(v.version)} data-testid={`restore-v${v.version}`}>
                      <RotateCcw className="h-3.5 w-3.5" /> Restore v{v.version}
                    </Button>
                  )}
                </div>
                {v.diff ? (
                  <div className="mt-2 space-y-1.5">
                    {Object.entries(v.diff.changes).filter(([f]) => f !== "suite_id").map(([f, c]) => (
                      <p key={f} className="text-xs"><span className="text-muted-foreground">{f.replace(/_/g, " ")}:</span> {String(c.from ?? "-")} to {String(c.to ?? "-")}</p>
                    ))}
                    <SqlDiff lines={v.diff.sql_diff} />
                  </div>
                ) : <pre className="mt-2 max-h-32 overflow-auto rounded-lg bg-muted/50 p-2 font-mono text-[11px]">{v.sql_text}</pre>}
              </li>
            ))}
          </ol>
        )}
      </Modal>
    </>
  );
}
