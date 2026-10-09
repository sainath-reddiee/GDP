"use client";

import { createContext, useContext, type ReactNode } from "react";
import { Eye } from "lucide-react";

type Access = { governance: boolean; privileges: string[]; readOnly: boolean };
const AccessCtx = createContext<Access>({ governance: false, privileges: [], readOnly: false });

export function AccessProvider({ value, children }: { value: Access; children: ReactNode }) {
  return <AccessCtx.Provider value={value}>{children}</AccessCtx.Provider>;
}

/** What the signed-in user may do. Everything is allowed until governance is deployed. */
export function useAccess() {
  const a = useContext(AccessCtx);
  const can = (privilege: string) => !a.governance || a.privileges.includes("*") || a.privileges.includes(privilege);
  /** May act directly, or raise a request that goes to the approver role. */
  const canAct = (privilege: string) => can(privilege) || can("REQUEST.CHANGES");
  return { ...a, can, canAct };
}

export function ReadOnlyBanner() {
  const { readOnly } = useAccess();
  if (!readOnly) return null;
  return (
    <div role="status" className="mb-4 flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
      <Eye className="h-3.5 w-3.5 shrink-0" />
      View-only access: you can see everything, but you cannot change, run or ask AI for anything. Ask a governance admin for a role.
    </div>
  );
}
