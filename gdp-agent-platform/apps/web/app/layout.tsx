import "./globals.css";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { AUTH_MODE, DEV_COOKIE, SESSION_COOKIE, whoami } from "@/lib/api";
import { NavProgress } from "@/components/nav-progress";
import { Sidebar } from "@/components/sidebar";

export const metadata = { title: "GDP Engineering Factory" };

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
        <div className="grid min-h-screen grid-cols-[240px_1fr]">
          <Sidebar user={me?.user ?? null} role={me?.role ?? null} canLogout />
          <main className="min-w-0 bg-background px-8 py-7">{children}</main>
        </div>
      </body>
    </html>
  );
}
