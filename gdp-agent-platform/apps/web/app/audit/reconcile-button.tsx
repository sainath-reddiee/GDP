"use client";

import { useState, useTransition } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { reconcileCosts, type ReconcileResult } from "@/app/admin/actions";

/** Pulls actual credits from Snowflake's Cortex usage views; explains when the role cannot read them. */
export function ReconcileButton({ last }: { last?: ReconcileResult | null }) {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<ReconcileResult | null>(last ?? null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
      <Button size="sm" variant="outline" disabled={pending}
              onClick={() => start(async () => {
                setError(null);
                const r = await reconcileCosts();
                if (r.ok) setResult(r.data); else setError(r.error);
              })}>
        <RefreshCw className={pending ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
        {pending ? "Reconciling" : "Reconcile with Snowflake billing"}
      </Button>
      {result && result.access && (
        <span>{result.reconciled} calls matched to billed credits · {result.awaiting_billing} awaiting billing{result.at ? ` · ${result.at}` : ""}</span>
      )}
      {result && !result.access && <span className="text-amber-700 dark:text-amber-400">{result.detail?.split(". ")[0]}. Estimates are shown meanwhile.</span>}
      {error && <span className="text-destructive">{error}</span>}
    </div>
  );
}
