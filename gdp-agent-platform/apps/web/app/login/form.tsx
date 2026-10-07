"use client";

import { useId, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { useFormState } from "react-dom";
import { AlertCircle, Eye, EyeOff, ExternalLink, KeyRound, ShieldCheck, Snowflake, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { continueDev, login } from "./actions";
import { LoginButton } from "./login-button";

export type AuthMode = "pat" | "dev";

const TOKEN_DOCS = "https://docs.snowflake.com/en/user-guide/programmatic-access-tokens";

/** Input with a label that floats above the value once focused or filled. */
function FloatingField({ label, icon, trailing, className, ...props }: InputHTMLAttributes<HTMLInputElement> & {
  label: string; icon: ReactNode; trailing?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="group relative">
      <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500 transition-colors group-focus-within:text-indigo-400">
        {icon}
      </span>
      <input
        id={id}
        placeholder=" "
        className={cn(
          "peer h-[52px] w-full rounded-xl border border-slate-800 bg-slate-950/60 pb-1.5 pl-10 pr-11 pt-5 text-sm text-slate-100",
          "outline-none transition placeholder:text-transparent hover:border-slate-700",
          "focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40",
          className,
        )}
        {...props}
      />
      <label
        htmlFor={id}
        className={cn(
          "pointer-events-none absolute left-10 top-1/2 -translate-y-1/2 text-sm text-slate-500 transition-all",
          "peer-focus:top-3.5 peer-focus:text-[11px] peer-focus:text-indigo-300",
          "peer-[:not(:placeholder-shown)]:top-3.5 peer-[:not(:placeholder-shown)]:text-[11px]",
        )}
      >
        {label}
      </label>
      {trailing && <span className="absolute right-2 top-1/2 -translate-y-1/2">{trailing}</span>}
    </div>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-md border border-slate-700 bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
      {children}
    </kbd>
  );
}

/** Snowflake user + programmatic access token. The token is exchanged for a session and never stored. */
function TokenForm() {
  const [state, action] = useFormState(login, null);
  const [show, setShow] = useState(false);
  return (
    <form action={action} className="space-y-3">
      <FloatingField label="Snowflake user" name="user" required autoComplete="username" spellCheck={false}
                     autoCapitalize="none" icon={<User className="h-4 w-4" />} />
      <FloatingField
        label="Programmatic access token"
        name="token"
        type={show ? "text" : "password"}
        required
        autoComplete="off"
        spellCheck={false}
        icon={<KeyRound className="h-4 w-4" />}
        trailing={(
          <button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide token" : "Show token"}
                  className="rounded-lg p-2 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200">
            {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        )}
      />
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="flex items-center gap-1.5 text-slate-500">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-400/80" /> Used for this session only, never stored
        </span>
        <a href={TOKEN_DOCS} target="_blank" rel="noreferrer"
           className="inline-flex items-center gap-1 font-medium text-indigo-300 transition hover:text-indigo-200">
          Need a token? <ExternalLink className="h-3 w-3" />
        </a>
      </div>
      {state && !state.ok && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {state.error}
        </p>
      )}
      <LoginButton label="Sign in" pendingLabel="Verifying with Snowflake…" className="mt-1" />
      <p className="text-center text-[11px] text-slate-500">
        Press <Kbd>↵</Kbd> to continue
      </p>
    </form>
  );
}

/** Development mode: the API holds a Snowflake browser-SSO session, so signing in opens the workspace with it. */
function SsoForm() {
  return (
    <form action={continueDev} className="space-y-3">
      <LoginButton label="Continue with Snowflake SSO" pendingLabel="Opening your workspace…"
                   icon={<Snowflake className="h-4 w-4" />} />
      <p className="text-center text-xs leading-relaxed text-slate-500">
        Uses the Snowflake browser sign-in of this workspace. Your Snowflake role and grants decide what you can see.
      </p>
      <p className="text-center text-[11px] text-slate-500">
        Press <Kbd>↵</Kbd> to continue
      </p>
    </form>
  );
}

export function LoginForm({ mode }: { mode: AuthMode }) {
  return (
    <div className="space-y-5">
      {mode === "pat" ? <TokenForm /> : <SsoForm />}
      <div className="flex items-center gap-3 text-[11px] uppercase tracking-[0.14em] text-slate-600">
        <span className="h-px flex-1 bg-slate-800" />
        {mode === "pat" ? "Token sign-in" : "Single sign-on"}
        <span className="h-px flex-1 bg-slate-800" />
      </div>
      <ul className="grid gap-2 text-xs text-slate-400">
        <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-400" /> Profiling and modeling run in your Snowflake account with Cortex</li>
        <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Every mapping, STTM and code change is approved by a person</li>
      </ul>
    </div>
  );
}
