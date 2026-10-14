"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus } from "lucide-react";
import { useAccess } from "@/components/access";
import { createGoldBuild } from "./actions";

/** Start a gold build for a domain. */
export function NewGoldBuild({ domains }: { domains: { domain_id: string; domain_name: string }[] }) {
  const { canAct } = useAccess();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [domain, setDomain] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  if (!canAct("MODEL.EDIT")) return null;
  const go = () => start(async () => {
    setError("");
    const r = await createGoldBuild(domain);
    if (!r.ok) { setError(r.error); return; }
    router.push(`/gold/${r.data.build_id}`);
  });
  return open ? (
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Domain" value={domain} onChange={(e) => setDomain(e.target.value)} className="h-9 rounded-lg border bg-background px-2 text-sm">
        <option value="">Choose a domain…</option>
        {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
      </select>
      <button type="button" disabled={!domain || pending} onClick={go} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
        {pending && <Loader2 className="h-4 w-4 animate-spin" />}Start</button>
      <button type="button" onClick={() => setOpen(false)} className="h-9 rounded-lg px-2 text-sm hover:bg-muted">Cancel</button>
      {error && <p role="alert" className="w-full text-xs text-destructive">{error}</p>}
    </div>
  ) : (
    <button type="button" onClick={() => setOpen(true)} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground">
      <Plus className="h-4 w-4" />New gold build</button>
  );
}
