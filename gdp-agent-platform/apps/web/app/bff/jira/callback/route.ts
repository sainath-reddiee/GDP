import { NextResponse, type NextRequest } from "next/server";
import { isRedirectError } from "next/dist/client/components/redirect";
import { api, ApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

/** Atlassian sends the engineer back here after they approve the app. The API checks the one-time state belongs to the
 *  same signed-in user, exchanges the code and stores the (encrypted) refresh token; then we return to where they were. */
export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const back = (path: string, params: Record<string, string>) => {
    // a same-site path only: "/x", never "//host", "/\\host" or control characters
    const safe = /^\/(?![\/\\])[^\\\s]*$/.test(path) ? path : "/";
    const target = new URL(safe, url.origin);
    if (target.origin !== url.origin) return NextResponse.redirect(new URL("/", url.origin));
    for (const [k, v] of Object.entries(params)) target.searchParams.set(k, v);
    return NextResponse.redirect(target);
  };
  const error = url.searchParams.get("error");
  if (error) return back("/admin", { section: "integrations", view: "jira", jira: "denied", reason: url.searchParams.get("error_description") ?? error });
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  if (!code || !state) return back("/admin", { section: "integrations", view: "jira", jira: "error", reason: "Atlassian did not return a sign-in code" });
  try {
    const done = await api<{ return_to: string; display_name: string; site_url: string }>("/api/jira/callback", {
      method: "POST", body: JSON.stringify({ code, state }),
    });
    return back(done.return_to || "/", { jira: "connected" });
  } catch (e) {
    if (isRedirectError(e)) throw e;  // signed out: api() sends the user to /login
    return back("/admin", { section: "integrations", view: "jira", jira: "error", reason: e instanceof ApiError ? e.message : "sign-in failed" });
  }
}
