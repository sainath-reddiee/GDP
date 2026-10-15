import { NextResponse, type NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  // Airflow push events carry their own HMAC signature and never a user session; /bff/version only names the build
  if (path.startsWith("/login") || path === "/bff/signout" || path === "/bff/ops/ingest" || path === "/bff/version") {
    return NextResponse.next();
  }
  const auth = process.env.AIP_AUTH ?? "dev";
  const signedIn = auth === "pat" ? req.cookies.get("aip_session") : req.cookies.get("aip_dev");
  if (signedIn) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", req.url));
}

// the favicon (app/icon.svg) is public: the login page shows it before anyone signs in
export const config = { matcher: ["/((?!_next|favicon.ico|icon.svg).*)"] };
