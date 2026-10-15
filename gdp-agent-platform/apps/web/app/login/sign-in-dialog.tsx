"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, X } from "lucide-react";
import { LogoMark } from "@/components/brand/logo-mark";
import { PRODUCT } from "@/lib/brand";
import { LoginForm, type AuthMode } from "./form";

/** The sign-in form as a small dialog over the landing page: opened from the top bar or a call to action. */
export function SignInDialog({ open, onClose, mode, expired }: { open: boolean; onClose: () => void; mode: AuthMode; expired: boolean }) {
  const panel = useRef<HTMLDivElement>(null);
  // the dialog is a portal on <body>, so it renders only after hydration (the server has no document)
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!open || !mounted) return;
    const prev = document.activeElement as HTMLElement | null;
    // focus the first control, so Enter signs in straight away
    const t = setTimeout(() => panel.current?.querySelector<HTMLElement>("input, button[type=submit]")?.focus(), 30);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.documentElement.style.overflow = "hidden";
    return () => {
      clearTimeout(t);
      document.removeEventListener("keydown", onKey);
      document.documentElement.style.overflow = "";
      prev?.focus?.();
    };
  }, [open, mounted, onClose]);

  if (!open || !mounted) return null;
  return createPortal(
    <div className="fixed inset-0 z-[60] grid place-items-center p-4" role="dialog" aria-modal="true" aria-labelledby="sign-in-title">
      <button type="button" aria-label="Close sign-in" onClick={onClose}
              className="absolute inset-0 cursor-default bg-[#04060c]/75 backdrop-blur-sm animate-fade-up" />
      <div ref={panel} data-testid="sign-in-dialog"
           className="relative w-full max-w-[380px] animate-pop-in rounded-2xl border border-slate-700/60 bg-[#0b1020]/95 p-6 text-slate-200 shadow-[0_40px_120px_-30px_rgba(79,70,229,0.6)]">
        <button type="button" onClick={onClose} aria-label="Close"
                className="absolute right-3 top-3 rounded-lg p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-200">
          <X className="h-4 w-4" />
        </button>
        <div className="flex items-center gap-3">
          <LogoMark size="sm" />
          <div>
            <h2 id="sign-in-title" className="text-base font-semibold text-white">Sign in</h2>
            <p className="text-xs text-slate-500">{PRODUCT}</p>
          </div>
        </div>
        {expired && (
          <p role="status" className="mt-4 flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Your session ended. Sign in again to continue.
          </p>
        )}
        <div className="mt-5">
          <LoginForm mode={mode} compact />
        </div>
      </div>
    </div>,
    document.body,
  );
}
