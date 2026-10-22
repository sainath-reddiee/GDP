import Link from "next/link";
import { Radar, Settings2 } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { SkillsCatalog } from "./skills-catalog";
import type { SkillsResponse } from "./types";

export default async function Skills() {
  const [data, proposals] = await Promise.all([
    api<SkillsResponse>("/api/skills"),
    api<{ counts: Record<string, number>; available: boolean }>("/api/skill-proposals?limit=200").catch(() => null),
  ]);
  const waiting = proposals?.available ? proposals.counts.PROPOSED ?? 0 : null;
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Enterprise Memory" title="Agent Skills"
                  description="Playbooks the agents load before each stage. Every change is a new version; the production label decides what runs use, and a candidate can be tried on one run first."
                  actions={<>
                    {waiting !== null && (
                      <Link href="/skills/proposed" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-sm hover:bg-muted" data-testid="proposed-skills-link">
                        <Radar className="h-4 w-4 text-agent" />Proposed skills
                        {waiting > 0 && <span className="rounded-full bg-agent/10 px-1.5 text-xs tabular-nums text-agent">{waiting}</span>}
                      </Link>
                    )}
                    <Link href="/admin?section=skills" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-sm hover:bg-muted">
                      <Settings2 className="h-4 w-4" />Skills per stage
                    </Link>
                  </>} />
      <SkillsCatalog data={data} />
    </div>
  );
}
