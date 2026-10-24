import { NextResponse, type NextRequest } from "next/server";
import { PATH_HEADER } from "@/lib/os/access";

export function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  // the home page is public (the product page when signed out, app/page.tsx); Airflow push events carry their own HMAC
  // signature and never a user session; /bff/version only names the build
  if (path === "/" || path.startsWith("/login") || path === "/bff/signout" || path === "/bff/ops/ingest" || path === "/bff/version") {
    return NextResponse.next();
  }
  const auth = process.env.AIP_AUTH ?? "dev";
  const signedIn = auth === "pat" ? req.cookies.get("aip_session") : req.cookies.get("aip_dev");
  if (!signedIn) return NextResponse.redirect(new URL("/login", req.url));
  // the root layout gates every page by the person's privileges (lib/os/access), so it needs the path; set here,
  // never taken from the client
  const headers = new Headers(req.headers);
  headers.set(PATH_HEADER, path);
  return NextResponse.next({ request: { headers } });
}

// the favicon (app/icon.svg) is public: the login page shows it before anyone signs in
export const config = { matcher: ["/((?!_next|favicon.ico|icon.svg).*)"] };
