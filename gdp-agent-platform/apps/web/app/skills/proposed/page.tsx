import Link from "next/link";
import { api, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { listProposals, type ScoutSettings } from "./actions";
import { ProposedView } from "./proposed-view";

export const metadata = { title: "Proposed skills" };

/** Skills the platform proposes from repeated, approved work: generalised so they carry to another client, tested
 *  against what the stage uses today, and filed as drafts nothing loads until a person promotes one. */
export default async function ProposedSkillsPage() {
  const me = await whoami();
  const [r, settings] = await Promise.all([
    listProposals(),
    api<ScoutSettings>("/api/skill-proposals/settings").catch(() => null),
  ]);
  return (
    <div className="space-y-5">
      <p className="eyebrow"><Link href="/skills" className="hover:underline">Skills</Link></p>
      <PageHeader eyebrow="Knowledge" title="Proposed skills"
                  description="The skill scout watches for approved knowledge that keeps being used, turns each theme into a playbook any client can use (names become placeholders), and tests it before proposing it. Nothing loads a proposal until a person promotes it." />
      {!r.ok ? <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{r.error}</p>
        : !r.data.available ? <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">The skill scout needs the latest deploy (migration V067).</p>
        : <ProposedView items={r.data.items} canEdit={can(me, "SKILL.EDIT")} canRelease={can(me, "SKILL.RELEASE")} canAi={can(me, "AI.USE")}
                        canConfig={can(me, "CONFIG.EDIT")} settings={settings} />}
    </div>
  );
}
