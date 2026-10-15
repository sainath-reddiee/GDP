import { AUTH_MODE } from "@/lib/api";
import { PRODUCT } from "@/lib/brand";
import type { AuthMode } from "./form";
import { Landing } from "./landing";

export const metadata = {
  title: { absolute: `${PRODUCT} · Sign in` },
  description: "Agentic data engineering on Snowflake: AI agents take any source to a reviewed dbt pull request, with human approval at every gate.",
};

/** Signed out, this is the front door: what the platform does and what a team gets from it, with sign-in in the top
 * bar (a small dialog). An ended session (/bff/signout sends ?expired=1) opens the dialog straight away. */
export default function LoginPage({ searchParams }: { searchParams?: { expired?: string } }) {
  const mode: AuthMode = AUTH_MODE === "pat" ? "pat" : "dev";
  return <Landing mode={mode} expired={searchParams?.expired === "1"} />;
}
