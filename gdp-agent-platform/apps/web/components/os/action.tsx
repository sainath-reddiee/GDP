import type { ReactNode } from "react";
import { Lock, Send } from "lucide-react";
import { ACTIONS, actMode, type ActionId } from "@/lib/os/access";
import type { OsAccess } from "@/lib/os/session";
import { Btn } from "./ui";

/** A button that changes something, shown by governance: as itself when the person holds the privilege, as
 *  "Request approval: ..." (routed to the approver role, as the classic pages do) when they may raise change requests,
 *  and otherwise hidden, or with `denied="explain"` shown disabled with the privilege it needs. The page it opens
 *  enforces the same privilege. */
export function Action({ access, id, privilege, href, label, icon, variant = "default", external, denied = "hide" }: {
  access: OsAccess; id: ActionId; privilege?: string; href: string; label?: string; icon?: ReactNode;
  variant?: "default" | "primary" | "mint" | "warn" | "red"; external?: boolean; denied?: "hide" | "explain";
}) {
  const def = ACTIONS[id];
  const need = privilege ?? def.privilege;
  const text = label ?? def.label;
  const mode = actMode(access, need);
  if (mode === "none") {
    if (denied === "hide") return null;
    return (
      <span aria-disabled="true" title={`Needs ${need}. Ask a governance admin for a persona that holds it.`}
            className="os-btn cursor-not-allowed opacity-50"><Lock className="h-3.5 w-3.5" />{text}</span>
    );
  }
  if (mode === "request") {
    const to = access.approvers[need];
    return (
      <Btn href={href} variant="warn" external={external}
           title={`You do not hold ${need}. Your change goes to ${to ? `the ${to.replace(/_/g, " ").toLowerCase()} role` : "an approver"} for approval.`}>
        <Send className="h-3.5 w-3.5" /> Request approval: {text.charAt(0).toLowerCase() + text.slice(1)}
      </Btn>
    );
  }
  return <Btn href={href} variant={variant} external={external}>{icon}{text}</Btn>;
}
