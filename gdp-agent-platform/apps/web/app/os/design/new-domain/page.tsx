import { redirect } from "next/navigation";

/** Onboarding a domain moved to Enable (the Data OS Administrator's); links from before land there. */
export default function MovedNewDomain({ searchParams }: { searchParams: Record<string, string | undefined> }) {
  const keep = new URLSearchParams(Object.entries(searchParams).filter((e): e is [string, string] => !!e[1]));
  redirect(`/os/enable/new-domain${keep.size ? `?${keep}` : ""}`);
}
