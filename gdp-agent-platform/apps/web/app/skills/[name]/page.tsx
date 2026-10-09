import Link from "next/link";
import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { SkillDetail, SkillsResponse } from "../types";
import { SkillView } from "./skill-view";

export default async function SkillPage({ params, searchParams }: {
  params: { name: string }; searchParams?: { version?: string; tab?: string };
}) {
  const name = decodeURIComponent(params.name);
  const qs = searchParams?.version ? `?version=${encodeURIComponent(searchParams.version)}` : "";
  let detail: SkillDetail;
  try {
    detail = await api<SkillDetail>(`/api/skills/${encodeURIComponent(name)}${qs}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const catalog = await api<SkillsResponse>("/api/skills").catch(() => null);
  const card = catalog?.skills.find((s) => s.skill_name === detail.skill_name) ?? null;
  return (
    <div className="space-y-4">
      <p className="eyebrow">
        <Link href="/skills" className="hover:underline">Skills</Link>
        <span className="mx-1.5 text-muted-foreground">/</span>
        <span className="font-mono normal-case tracking-normal text-muted-foreground">{detail.skill_name}</span>
      </p>
      <SkillView detail={detail} card={card} categories={catalog?.categories ?? []} tab={searchParams?.tab ?? "overview"} />
    </div>
  );
}
