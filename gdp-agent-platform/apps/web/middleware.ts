import { NextResponse, type NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  if (req.nextUrl.pathname.startsWith("/login") || req.nextUrl.pathname === "/bff/signout") return NextResponse.next();
  const auth = process.env.AIP_AUTH ?? "dev";
  const signedIn = auth === "pat" ? req.cookies.get("aip_session") : req.cookies.get("aip_dev");
  if (signedIn) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", req.url));
}

export const config = { matcher: ["/((?!_next|favicon.ico).*)"] };
