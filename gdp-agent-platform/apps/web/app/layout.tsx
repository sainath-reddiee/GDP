import "./globals.css";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { api, AUTH_MODE, DEV_COOKIE, SESSION_COOKIE, whoami } from "@/lib/api";
import { CatalogDisplayProvider } from "@/components/catalog-display-provider";
import { configureCatalogDisplay, type CatalogDisplayConfig } from "@/lib/catalog-display";
import { CopilotProvider } from "@/components/copilot/copilot-provider";
import { NavProgress } from "@/components/nav-progress";
import { Sidebar } from "@/components/sidebar";

export const metadata = { title: "Agentic pipeline" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const jar = cookies();
  const signedIn = AUTH_MODE === "pat" ? !!jar.get(SESSION_COOKIE) : !!jar.get(DEV_COOKIE);
  if (!signedIn) {
    return (
      <html lang="en">
        <body>{children}</body>
      </html>
    );
  }
  const [me, catalog] = await Promise.all([
    whoami(),
    api<CatalogDisplayConfig>("/api/config/catalog-display").catch(() => null),
  ]);
  configureCatalogDisplay(catalog);
  return (
    <html lang="en">
      <body>
        <NavProgress />
        <div className="flex min-h-screen">
          <Sidebar user={me?.user ?? null} role={me?.role ?? null} canLogout
                   privileges={me?.governance ? me.privileges ?? [] : null} appRoles={me?.governance ? me.granted_roles ?? [] : null} />
          <main className="min-w-0 flex-1 bg-background">
            <CopilotProvider>
              <div className="mx-auto w-full max-w-[1600px] px-6 py-7 lg:px-10">
                <CatalogDisplayProvider config={catalog}>{children}</CatalogDisplayProvider>
              </div>
            </CopilotProvider>
          </main>
        </div>
      </body>
    </html>
  );
}
