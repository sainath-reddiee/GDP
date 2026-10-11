import type { ReactNode } from "react";
import { HubTabs } from "@/components/hub/hub-tabs";

/** Every lifecycle hub shares one navigation row; /runs/<id> keeps its own shell. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <HubTabs />
      {children}
    </>
  );
}
