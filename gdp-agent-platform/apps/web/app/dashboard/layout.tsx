import type { ReactNode } from "react";

/** The asset stores and hubs under /dashboard; the sidebar is the one place to move between them. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
