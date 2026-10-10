import { NextResponse, type NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  // Airflow push events carry their own HMAC signature and never a user session
  if (path.startsWith("/login") || path === "/bff/signout" || path === "/bff/ops/ingest") return NextResponse.next();
  const auth = process.env.AIP_AUTH ?? "dev";
  const signedIn = auth === "pat" ? req.cookies.get("aip_session") : req.cookies.get("aip_dev");
  if (signedIn) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", req.url));
}

export const config = { matcher: ["/((?!_next|favicon.ico).*)"] };
