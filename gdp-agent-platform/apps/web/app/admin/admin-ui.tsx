"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Loader2, RotateCcw, Save, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Tone = "ok" | "error";
type Toast = { id: number; tone: Tone; text: string };
const ToastCtx = createContext<(tone: Tone, text: string) => void>(() => undefined);

/** Toasts for the Admin page: saves, resets, tests. Errors stay longer. */
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Tone, text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 9000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} role="status"
               className={cn("pointer-events-auto flex items-start gap-2 rounded-xl border bg-card p-3 text-sm shadow-lg",
                 t.tone === "error" ? "border-destructive/30" : "border-success/30")}>
            {t.tone === "error"
              ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />}
            <p className="min-w-0 flex-1 break-words">{t.text}</p>
            <button type="button" aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              <X className="h-4 w-4 text-muted-foreground" />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

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
