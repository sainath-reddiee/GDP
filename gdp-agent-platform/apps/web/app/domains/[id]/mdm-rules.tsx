"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Check, CircleDashed, Loader2 } from "lucide-react";
import { LAYER_META } from "@/lib/layers";
import { cn } from "@/lib/utils";
import { mdmLayer } from "../actions";

/** The MDM (Reltio) layer of a mastered domain, as the platform uses it: whether the contract is complete, which domains
 *  consume it, and the MDM rules every run that reads the layer is given (the cross-reference gotchas). */
export function MdmRules({ domainId }: { domainId: string }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof mdmLayer>> | null>(null);
  useEffect(() => { void mdmLayer(domainId).then(setData); }, [domainId]);
  if (!data) return <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the MDM layer…</p>;
  if (!data.ok) return <p role="alert" className="text-sm text-destructive">{data.error}</p>;
  return (
    <div className="space-y-4" data-testid="mdm-layer">
      <section className="surface p-4">
        <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <span className={cn("rounded px-1.5 py-0.5 text-[11px]", LAYER_META.MDM.chip)}>MDM (Reltio)</span>What the platform relies on</h3>
        <ul className="grid gap-2 sm:grid-cols-3">
          {data.data.readiness.map((i) => (
            <li key={i.id} className={cn("rounded-lg border px-3 py-2 text-xs", i.met ? "border-emerald-500/40" : "border-amber-500/40")}>
              <p className="flex items-center gap-1.5 font-medium">{i.met ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <CircleDashed className="h-3.5 w-3.5 text-amber-600" />}{i.label}</p>
              <p className="mt-0.5 text-muted-foreground">{i.detail}</p>
            </li>
          ))}
        </ul>
      </section>
      <section className="surface p-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-semibold">MDM rules ({data.data.rules.length})</h3>
          <Link href={`/knowledge?tab=items&domain_id=${domainId}&type=EXCEPTION`} className="text-xs text-primary hover:underline">Edit or add in Knowledge</Link>
        </div>
        <p className="text-xs text-muted-foreground">Read by every mapping, STTM and code step that resolves this domain&apos;s mastered keys, in both pipelines.</p>
        <ul className="mt-2 divide-y">
          {data.data.rules.map((r) => (
            <li key={r.knowledge_id} className="py-2 text-xs"><p className="font-medium">{r.title}</p><p className="line-clamp-2 text-muted-foreground">{r.content}</p></li>
          ))}
          {!data.data.rules.length && <li className="py-2 text-xs text-muted-foreground">No MDM rules yet.</li>}
        </ul>
      </section>
    </div>
  );
}
