"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, Loader2, ShieldCheck, Undo2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { approveRequest, cancelRequest, rejectRequest, type ChangeRequest } from "../governance-actions";

const TONE: Record<string, "warning" | "success" | "destructive" | "outline"> = {
  PENDING: "warning", APPLIED: "success", APPROVED: "success", FAILED: "destructive", REJECTED: "destructive", CANCELLED: "outline",
};

function Row({ r, me }: { r: ChangeRequest; me: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, start] = useTransition();
  const act = (kind: "approve" | "reject" | "cancel") => start(async () => {
    setMsg(null);
    const res = kind === "approve" ? await approveRequest(r.request_id, note)
      : kind === "reject" ? await rejectRequest(r.request_id, note) : await cancelRequest(r.request_id);
    if (!res.ok) { setMsg({ ok: false, text: res.error }); return; }
    if (kind === "approve") {
      const d = res.data as { status: string; status_code: number };
      setMsg({ ok: d.status === "APPLIED", text: d.status === "APPLIED" ? "Approved and applied."
        : `Approved, but applying failed (${d.status_code}). See the result below.` });
    }
    router.refresh();
  });
  const mine = r.requested_by === me;
  return (
    <div className={cn("overflow-hidden rounded-xl border border-l-4 bg-card",
                       r.status === "PENDING" ? "border-l-warning" : r.status === "APPLIED" ? "border-l-success"
                         : r.status === "FAILED" || r.status === "REJECTED" ? "border-l-destructive" : "border-l-muted")}>
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-start gap-3 px-4 py-3 text-left">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-violet-600" />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{r.title || r.privilege}</span>
            <Badge variant={TONE[r.status] ?? "outline"}>{r.status.toLowerCase()}</Badge>
            <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-[10px]">{r.privilege}</span>
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{r.summary}</span>
          <span className="mt-0.5 block text-[11px] text-muted-foreground">
            {mine ? "You" : r.requested_by} asked {r.created_at.slice(0, 16)} · for {r.approver_role.toLowerCase().replace(/_/g, " ")}
            {r.decided_by ? ` · ${r.status.toLowerCase()} by ${r.decided_by}` : ""}
          </span>
        </span>
        <ChevronDown className={cn("mt-1 h-4 w-4 text-muted-foreground transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3 border-t bg-muted/10 px-4 pb-4 pt-3 text-xs">
          <p>
            <span className="text-muted-foreground">Call: </span><span className="font-mono">{r.method} {r.path}</span>
            {r.run_id && <> · <Link href={`/runs/${r.run_id}`} className="text-primary hover:underline">open run</Link></>}
          </p>
          <div>
            <p className="mb-1 font-semibold">What will change</p>
            <pre className="max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] text-slate-100">
              {r.payload == null ? "(no body)" : JSON.stringify(r.payload, null, 2)}
            </pre>
          </div>
          {r.decision_note && <p><span className="text-muted-foreground">Note: </span>{r.decision_note}</p>}
          {r.result && (
            <div>
              <p className="mb-1 font-semibold">Result ({r.result.status_code})</p>
              <pre className="max-h-48 overflow-auto rounded-lg bg-muted p-3 font-mono text-[11px]">{JSON.stringify(r.result.body, null, 2)}</pre>
            </div>
          )}
          {r.status === "PENDING" && (r.can_decide || mine) && (
            <div className="space-y-2">
              {r.can_decide && (
                <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (required to reject)" aria-label="Decision note" />
              )}
              <div className="flex flex-wrap gap-2">
                {r.can_decide && (
                  <Button size="sm" disabled={busy} onClick={() => act("approve")}>
                    {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Approve and apply
                  </Button>
                )}
                {r.can_decide && (
                  <Button size="sm" variant="outline" disabled={busy || !note.trim()} onClick={() => act("reject")}>
                    <X className="h-3.5 w-3.5" />Reject
                  </Button>
                )}
                {mine && (
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => act("cancel")}>
                    <Undo2 className="h-3.5 w-3.5" />Cancel request
                  </Button>
                )}
              </div>
            </div>
          )}
          {msg && <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "text-success" : "text-destructive"}>{msg.text}</p>}
        </div>
      )}
    </div>
  );
}

export function RequestList({ requests, scope, me }: { requests: ChangeRequest[]; scope: string; me: string }) {
  if (!requests.length) {
    return (
      <p className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
        {scope === "inbox" ? "Nothing is waiting for your approval." : "No requests yet."}
      </p>
    );
  }
  return <div className="space-y-2">{requests.map((r) => <Row key={r.request_id} r={r} me={me} />)}</div>;
}
