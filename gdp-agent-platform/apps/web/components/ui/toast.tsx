"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastTone = "ok" | "error" | "info";
type Toast = { id: number; tone: ToastTone; text: string };
const ToastCtx = createContext<(tone: ToastTone, text: string) => void>(() => undefined);

/** App-wide toasts: saves, approvals sent, test runs. Errors stay longer. Mounted once in the root layout. */
export const useToast = () => useContext(ToastCtx);

const ICON = { ok: CheckCircle2, error: AlertCircle, info: Info } as const;
const TONE = {
  ok: "border-success/30 [&>svg]:text-success",
  error: "border-destructive/30 [&>svg]:text-destructive",
  info: "border-primary/30 [&>svg]:text-primary",
} as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: ToastTone, text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 9000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-5 right-5 z-[70] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => {
          const Icon = ICON[t.tone];
          return (
            <div key={t.id} role="status"
                 className={cn("glass pointer-events-auto flex animate-fade-up items-start gap-2 p-3 text-sm shadow-pop", TONE[t.tone])}>
              <Icon className="mt-0.5 h-4 w-4 shrink-0" />
              <p className="min-w-0 flex-1 break-words">{t.text}</p>
              <button type="button" aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
                <X className="h-4 w-4 text-muted-foreground hover:text-foreground" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}
