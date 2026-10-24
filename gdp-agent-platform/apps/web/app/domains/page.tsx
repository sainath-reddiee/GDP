import { redirect } from "next/navigation";

export type { DomainCard } from "./domains-screen";

/** Domains live in the Data OS Knowledge hub. */
export default function Domains({ searchParams }: { searchParams?: { deleted?: string } }) {
  redirect(`/os/knowledge?view=domains${searchParams?.deleted === "1" ? "&deleted=1" : ""}`);
}
