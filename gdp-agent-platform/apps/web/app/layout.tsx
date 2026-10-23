import { AccessProvider, ReadOnlyBanner } from "@/components/access";
import "@fontsource-variable/inter";
import "./globals.css";
import "./theme-compat.css";
import "./os/ados.css";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { api, AUTH_MODE, DEV_COOKIE, SESSION_COOKIE } from "@/lib/api";
import { getDomainScope } from "@/lib/domain-scope";
import { CatalogDisplayProvider } from "@/components/catalog-display-provider";
import { configureCatalogDisplay, type CatalogDisplayConfig } from "@/lib/catalog-display";
import { CopilotProvider } from "@/components/copilot/copilot-provider";
import { NavProgress } from "@/components/nav-progress";
import { AppChrome } from "@/components/os/chrome";
import { CLASSIC_COOKIE, PATH_HEADER, SCREENS, canSee, mayUseClassic, screenOfPath } from "@/lib/os/access";
import { osAccess } from "@/lib/os/session";
import { Sidebar } from "@/components/sidebar";
import { CommandPalette } from "@/components/shell/command-palette";
import { AccountMenu, DomainSwitcher, NewMenu, TopBar, type DomainOption } from "@/components/shell/top-bar";
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
  const [access, catalog, domains] = await Promise.all([
    osAccess(),
    api<CatalogDisplayConfig>("/api/config/catalog-display").catch(() => null),
    api<{ domains: (DomainOption & { active_flag?: boolean })[] }>("/api/domains")
      .then((r) => (r.domains ?? []).filter((d) => d.active_flag !== false).map(({ domain_id, domain_name }) => ({ domain_id, domain_name })))
      .catch(() => [] as DomainOption[]),
  ]);
  const me = access.me;

  // every page is gated by the person's privileges (or the previewed role's): a page outside the role goes back to
  // My Work with the reason, before it reads anything (lib/os/access)
  const screen = screenOfPath(headers().get(PATH_HEADER) ?? "");
  if (screen && !canSee(access, screen.key)) redirect(`/os?denied=${screen.key}`);

  configureCatalogDisplay(catalog);
  // the classic pages see the grants on show: a previewed role's, so they hide what that role cannot do
  const privileges = access.governance ? access.privileges : null;
  const readOnly = access.preview ? access.preview === "VIEWER" : Boolean(me?.read_only);
  const classic = mayUseClassic(access.own) && jar.get(CLASSIC_COOKIE)?.value === "1";
  const ownRole = access.own.privileges.includes("*") ? "super admin" : "governance admin";
  const account = <AccountMenu user={me?.user ?? null} role={me?.role ?? null} canLogout appRoles={me?.governance ? me.granted_roles ?? [] : null} />;

  return (
    <html lang="en" suppressHydrationWarning>
      {head}
      <body>
        <ThemeProvider>
        <ToastProvider>
          <NavProgress />
          <AccessProvider value={{ governance: access.governance, privileges: access.privileges, readOnly }}>
          <CopilotProvider>
            <AppChrome
              classic={classic}
              shell={{
                user: access.user,
                environment: (process.env.AIP_ENVIRONMENT ?? "DEV").toUpperCase(),
                screens: SCREENS.filter((s) => s.rail !== false && canSee(access, s.key))
                  .map(({ key, href, label, exact, section }) => ({ key, href, label, exact, section })),
                personas: access.personas.map(({ id, label, short }) => ({ id, label, short })),
                persona: access.persona.id,
                preview: access.roles.length ? { current: access.preview, own: ownRole, roles: access.roles } : null,
                headerStart: <DomainSwitcher domains={domains} scope={getDomainScope()} />,
                headerEnd: <><NewMenu privileges={privileges} />{account}</>,
              }}
              sidebar={<Sidebar privileges={privileges} />}
              topBar={<TopBar user={me?.user ?? null} role={me?.role ?? null} canLogout appRoles={me?.governance ? me.granted_roles ?? [] : null}
                              privileges={privileges} domains={domains} scope={getDomainScope()} />}
              banner={<ReadOnlyBanner />}
              tabs={<WorkspaceTabs privileges={privileges} />}
            >
              <CatalogDisplayProvider config={catalog}>{children}</CatalogDisplayProvider>
            </AppChrome>
            <CommandPalette privileges={privileges} domains={domains} />
          </CopilotProvider>
          </AccessProvider>
        </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
