import "./ados.css";
import type { ReactNode } from "react";
import { whoami } from "@/lib/api";
import { currentPersona } from "@/lib/os/data";
import { OsShell } from "@/components/os/shell";

export const metadata = { title: "Mission control" };

/** The Agentic Data OS experience: its own shell over the same platform, session and data as the classic pages. */
export default async function OsLayout({ children }: { children: ReactNode }) {
  const me = await whoami();
  return (
    <OsShell user={me?.user ?? null} persona={currentPersona().id} environment={(process.env.AIP_ENVIRONMENT ?? "DEV").toUpperCase()}>
      {children}
    </OsShell>
  );
}
