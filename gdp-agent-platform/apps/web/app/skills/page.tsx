import Link from "next/link";
import { Settings2 } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { SkillsCatalog } from "./skills-catalog";
import type { SkillsResponse } from "./types";

export default async function Skills() {
  const data = await api<SkillsResponse>("/api/skills");
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Skills"
                  description="Playbooks the agents load before each stage. Every change is a new version; the production label decides what runs use, and a candidate can be tried on one run first."
                  actions={
                    <Link href="/admin?section=skills" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-sm hover:bg-muted">
                      <Settings2 className="h-4 w-4" />Skills per stage
                    </Link>
                  } />
      <SkillsCatalog data={data} />
    </div>
  );
}
