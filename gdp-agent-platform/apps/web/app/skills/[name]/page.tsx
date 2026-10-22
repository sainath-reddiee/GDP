import Link from "next/link";
import { Radar } from "lucide-react";
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
  const [catalog, scouted] = await Promise.all([
    api<SkillsResponse>("/api/skills").catch(() => null),
    api<{ proposal: { proposal_id: string; evidence_count: number; domain_count: number; transfer_label: string | null; state: string } | null }>(
      `/api/skill-proposals/for-skill/${encodeURIComponent(name)}`).catch(() => ({ proposal: null })),
  ]);
  const proposal = scouted.proposal;
  const card = catalog?.skills.find((s) => s.skill_name === detail.skill_name) ?? null;
  return (
    <div className="space-y-4">
      <p className="eyebrow">
        <Link href="/skills" className="hover:underline">Agent Skills</Link>
        <span className="mx-1.5 text-muted-foreground">/</span>
        <span className="font-mono normal-case tracking-normal text-muted-foreground">{detail.skill_name}</span>
      </p>
      {proposal && (
        <Link href="/skills/proposed" className="flex items-center gap-2 rounded-xl border border-agent/30 bg-agent/5 px-4 py-2.5 text-sm" data-testid="scout-banner">
          <Radar className="h-4 w-4 text-agent" />
          <span>Proposed by the skill scout from {proposal.evidence_count} approved knowledge items in {proposal.domain_count} domain{proposal.domain_count === 1 ? "" : "s"}
            {proposal.transfer_label ? ` · ${proposal.transfer_label}` : ""}. Nothing loads it until it is promoted and bound to a stage.</span>
        </Link>
      )}
      <SkillView detail={detail} card={card} categories={catalog?.categories ?? []} tab={searchParams?.tab ?? "overview"} />
    </div>
  );
}
