import { NextResponse, type NextRequest } from "next/server";
import { DEV_COOKIE, ROLE_COOKIE, SESSION_COOKIE } from "@/lib/api";

export const dynamic = "force-dynamic";

/** The backend said the session is over (expired or revoked): forget every sign-in cookie, then show the login page.
 *  Without this, an expired session cookie kept the app shell (sidebar, copilot, polling) around the login page. */
export async function GET(req: NextRequest) {
  const res = NextResponse.redirect(new URL("/login?expired=1", req.url));
  for (const name of [SESSION_COOKIE, DEV_COOKIE, ROLE_COOKIE]) res.cookies.delete(name);
  return res;
}
