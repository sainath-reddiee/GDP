import { AUTH_MODE } from "@/lib/api";
import { PRODUCT } from "@/lib/brand";
import type { AuthMode } from "./form";
import { Landing } from "./landing";

export const metadata = {
  title: { absolute: `${PRODUCT} · Sign in` },
  description: "Agentic data engineering on Snowflake: AI agents take any source to a reviewed dbt pull request, with human approval at every gate.",
};

/** Sign-in: the product page (app/page.tsx is the same page as the home) with the sign-in dialog already open, so a
 * link here, a protected page visited while signed out, or an ended session (/bff/signout sends ?expired=1) goes
 * straight to signing in. Closing the dialog leaves the person on the product page. */
export default function LoginPage({ searchParams }: { searchParams?: { expired?: string } }) {
  const mode: AuthMode = AUTH_MODE === "pat" ? "pat" : "dev";
  return <Landing mode={mode} expired={searchParams?.expired === "1"} signIn />;
}
