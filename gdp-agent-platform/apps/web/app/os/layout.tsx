import "./ados.css";
import type { ReactNode } from "react";
import { SCREENS, canSee } from "@/lib/os/access";
import { osAccess } from "@/lib/os/session";
import { OsShell } from "@/components/os/shell";

export const metadata = { title: "Mission control" };

/** The Agentic Data OS experience: its own shell over the same platform, session and data as the classic pages. The
 *  rail and "View as" carry only what the person's governance privileges (or a previewed role's) allow. */
export default async function OsLayout({ children }: { children: ReactNode }) {
  const access = await osAccess();
  const ownRole = access.own.privileges.includes("*") ? "super admin" : "governance admin";
  return (
    <OsShell
      user={access.user}
      environment={(process.env.AIP_ENVIRONMENT ?? "DEV").toUpperCase()}
      screens={SCREENS.filter((s) => canSee(access, s.key)).map(({ key, href, label, exact }) => ({ key, href, label, exact }))}
      personas={access.personas.map(({ id, label, short }) => ({ id, label, short }))}
      persona={access.persona.id}
      preview={access.roles.length ? { current: access.preview, own: ownRole, roles: access.roles } : null}
    >
      {children}
    </OsShell>
  );
}
