import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AUTH_MODE, DEV_COOKIE, SESSION_COOKIE } from "@/lib/api";
import { PRODUCT } from "@/lib/brand";
import type { AuthMode } from "./login/form";
import { Landing } from "./login/landing";

export const metadata = {
  title: { absolute: PRODUCT },
  description: "Agentic data engineering on Snowflake: AI agents take any source to a reviewed dbt pull request, with human approval at every gate.",
};

/** The home page. Signed out: the product page, with sign-in in the top bar. Signed in: My Work. */
export default function Home() {
  const jar = cookies();
  const signedIn = AUTH_MODE === "pat" ? !!jar.get(SESSION_COOKIE) : !!jar.get(DEV_COOKIE);
  if (signedIn) redirect("/work");
  const mode: AuthMode = AUTH_MODE === "pat" ? "pat" : "dev";
  return <Landing mode={mode} expired={false} />;
}
