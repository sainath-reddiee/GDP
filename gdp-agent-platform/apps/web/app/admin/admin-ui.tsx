"use client";

import { Loader2, RotateCcw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Toasts moved to components/ui/toast (mounted once in the root layout); Admin keeps importing them from here. */
export { ToastProvider, useToast } from "@/components/ui/toast";

/** Sticky "unsaved changes" bar: shown only while something differs from what is saved. */
export function DirtyBar({ count, pending, onSave, onDiscard, what = "change" }: {
  count: number; pending: boolean; onSave: () => void; onDiscard: () => void; what?: string;
}) {
  if (!count) return null;
  return (
    <div className="sticky bottom-4 z-30 mx-auto flex w-fit max-w-full flex-wrap items-center gap-3 rounded-full border bg-card px-4 py-2 text-sm shadow-lg">
      <span className="h-2 w-2 rounded-full bg-warning" />
      <span>{count} unsaved {what}{count === 1 ? "" : "s"}</span>
      <Button size="sm" variant="ghost" disabled={pending} onClick={onDiscard}><RotateCcw className="h-3.5 w-3.5" /> Discard</Button>
      <Button size="sm" disabled={pending} onClick={onSave}>
        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save
      </Button>
    </div>
  );
}
