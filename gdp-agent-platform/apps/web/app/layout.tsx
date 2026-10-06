import "./globals.css";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { AUTH_MODE, DEV_COOKIE, SESSION_COOKIE, whoami } from "@/lib/api";
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
  const me = await whoami();
  return (
    <html lang="en">
      <body>
        <NavProgress />
        <div className="flex min-h-screen">
          <Sidebar user={me?.user ?? null} role={me?.role ?? null} canLogout />
          <main className="min-w-0 flex-1 bg-background">
            <div className="mx-auto w-full max-w-[1600px] px-6 py-7 lg:px-10">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
