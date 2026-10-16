import { AccessProvider, ReadOnlyBanner } from "@/components/access";
import "@fontsource-variable/inter";
import "./globals.css";
import "./theme-compat.css";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { api, AUTH_MODE, DEV_COOKIE, SESSION_COOKIE, whoami } from "@/lib/api";
import { getDomainScope } from "@/lib/domain-scope";
import { CatalogDisplayProvider } from "@/components/catalog-display-provider";
import { configureCatalogDisplay, type CatalogDisplayConfig } from "@/lib/catalog-display";
import { CopilotProvider } from "@/components/copilot/copilot-provider";
import { NavProgress } from "@/components/nav-progress";
import { Sidebar } from "@/components/sidebar";
import { CommandPalette } from "@/components/shell/command-palette";
import { TopBar, type DomainOption } from "@/components/shell/top-bar";
import { WorkspaceTabs } from "@/components/shell/workspace-tabs";
import { THEME_SCRIPT, ThemeProvider } from "@/components/theme/theme-provider";
import { ToastProvider } from "@/components/ui/toast";
import { BUILD_SHA } from "@/lib/build";
import { PRODUCT, PRODUCT_SHORT } from "@/lib/brand";

export const metadata = { title: { default: PRODUCT, template: `%s · ${PRODUCT_SHORT}` }, other: { "aip-build": BUILD_SHA } };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const jar = cookies();
  const signedIn = AUTH_MODE === "pat" ? !!jar.get(SESSION_COOKIE) : !!jar.get(DEV_COOKIE);
  // the theme class is set before paint by THEME_SCRIPT, so <html> differs from the server render on purpose
  const head = <head><script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} /></head>;
  if (!signedIn) {
    return (
      <html lang="en" suppressHydrationWarning>
        {head}
        <body><ThemeProvider>{children}</ThemeProvider></body>
      </html>
    );
  }
  const [me, catalog, domains] = await Promise.all([
    whoami(),
    api<CatalogDisplayConfig>("/api/config/catalog-display").catch(() => null),
    api<{ domains: (DomainOption & { active_flag?: boolean })[] }>("/api/domains")
      .then((r) => (r.domains ?? []).filter((d) => d.active_flag !== false).map(({ domain_id, domain_name }) => ({ domain_id, domain_name })))
      .catch(() => [] as DomainOption[]),
  ]);
  configureCatalogDisplay(catalog);
  const privileges = me?.governance ? me.privileges ?? [] : null;
  return (
    <html lang="en" suppressHydrationWarning>
      {head}
      <body>
        <ThemeProvider>
        <ToastProvider>
          <NavProgress />
          <div className="flex min-h-screen">
            <Sidebar privileges={privileges} />
            <main className="min-w-0 flex-1">
              <AccessProvider value={{ governance: Boolean(me?.governance), privileges: me?.privileges ?? [], readOnly: Boolean(me?.read_only) }}>
              <CopilotProvider>
                <TopBar user={me?.user ?? null} role={me?.role ?? null} canLogout appRoles={me?.governance ? me.granted_roles ?? [] : null}
                        privileges={privileges} domains={domains} scope={getDomainScope()} />
                <div className="mx-auto w-full max-w-[1600px] px-6 py-7 lg:px-10">
                  <ReadOnlyBanner />
                  <WorkspaceTabs privileges={privileges} />
                  <CatalogDisplayProvider config={catalog}>{children}</CatalogDisplayProvider>
                </div>
                <CommandPalette privileges={privileges} domains={domains} />
              </CopilotProvider>
              </AccessProvider>
            </main>
          </div>
        </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
