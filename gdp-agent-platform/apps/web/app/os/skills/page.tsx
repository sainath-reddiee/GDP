import Link from "next/link";
import { ArrowRight, Boxes, FlaskConical, GitCompareArrows, History, Lightbulb, RotateCcw, ShieldCheck, Sparkles, TrendingUp } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadRuns, loadSkill, loadSkillDiff, loadSkillProposalFor, loadSkills } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { Action } from "@/components/os/action";
import { BuildEndToEnd } from "@/components/os/build-run";
import { leafRuns } from "@/lib/os/work";
import { ActivityRow, Banner, Bar, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { cn } from "@/lib/utils";
import { SkillLabel } from "@/components/os/decide";
import { ACTIONS, actMode } from "@/lib/os/access";

export const dynamic = "force-dynamic";
export const metadata = { title: "Skill studio" };

const rate = (n: number | undefined) => (n === undefined ? null : Math.round((n <= 1 ? n * 100 : n)));

export default async function SkillStudio({ searchParams }: { searchParams: { skill?: string; run?: string } }) {
  const access = await guard("skills");
  const persona = access.persona;
  const [skills, runs] = await Promise.all([loadSkills(), loadRuns("sort=updated&limit=150")]);
  const leaves = leafRuns(runs.data.runs);
  const list = skills.data.skills;
  const name = searchParams.skill
    ?? list.find((s) => s.candidate && s.production && s.candidate.skill_id !== s.production.skill_id)?.skill_name
    ?? [...list].sort((a, b) => b.loads_30d - a.loads_30d)[0]?.skill_name;
  const [detail, proposal] = name ? await Promise.all([loadSkill(name), loadSkillProposalFor(name)]) : [null, null];
  const d = detail?.data ?? null;
  const card = list.find((s) => s.skill_name === name);
  const versions = d?.versions ?? [];
  const prod = versions.find((v) => v.labels.includes("production")) ?? versions.find((v) => v.status === "ACTIVE") ?? versions[0];
  // the "after": the candidate when there is one, else the newest other version
  const after = versions.find((v) => v.labels.includes("candidate") && v.skill_id !== prod?.skill_id)
    ?? versions.filter((v) => v.skill_id !== prod?.skill_id).sort((a, b) => (b.revision ?? 0) - (a.revision ?? 0))[0];
  const diff = name && prod && after ? await loadSkillDiff(name, prod.skill_id, after.skill_id) : null;
  const skillMd = d?.selected?.files.find((f) => f.path === "SKILL.md")?.content ?? d?.selected?.files[0]?.content ?? "";
  const evalOf = (v?: typeof prod) => v?.eval_json ?? null;
  const testRun = evalOf(after) ?? evalOf(prod);
  const focusedRun = searchParams.run ? leaves.find((r) => r.run_id === searchParams.run) ?? runs.data.runs.find((r) => r.run_id === searchParams.run) : undefined;
  const failing = [
    ...(focusedRun ? [focusedRun] : []),
    ...leaves.filter((r) => !r.is_archived && r.run_id !== focusedRun?.run_id && (r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED")),
  ].slice(0, 5);
  const canOperate = actMode(access, "RUN.OPERATE") === "do";
  const stats = skills.data.stats;

  return (
    <>
      <PageHeader eyebrow={`${persona.label} / Skills and tooling`} title={<>Improve skill{name ? <span className="os-muted"> · {name.toLowerCase()}</span> : null}</>}
                  subtitle="Versioned, tested engineering skills the agents load at each stage. A fix becomes a reviewed skill version, then the affected steps rerun without redoing discovery."
                  actions={<>
                    {focusedRun && <Btn href={`/os/review?run=${encodeURIComponent(focusedRun.run_id)}`}>Validation gate</Btn>}
                    <Btn href="/os/skills/registry/proposed">Proposed skills</Btn>
                    {name && <Action access={access} id="editSkill" label="Edit and version in the registry" href={`/os/skills/registry/${encodeURIComponent(name)}`} variant="primary" icon={<Sparkles className="h-3.5 w-3.5" />} />}
                  </>} />
      <SourceError what="Skills registry" error={skills.error ?? detail?.error ?? null} />
      {after?.labels.includes("candidate") && (
        <Banner tone="amber" icon={Lightbulb} action={name ? <Action access={access} id="releaseSkill" href={`/os/skills/registry/${encodeURIComponent(name)}`} variant="warn" /> : undefined}>
          <b>Candidate v{after.version} (rev {after.revision}) is waiting for review</b> against production v{prod?.version}. It is promoted only after its tests pass and an owner approves.
        </Banner>
      )}
      {proposal?.data && <Banner tone="blue" icon={Sparkles}><b>The skill scout proposes an improvement.</b> {String(proposal.data.rationale ?? proposal.data.title ?? "")}</Banner>}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Skills in the registry" value={stats.skills ?? list.length} hint={`${stats.in_production ?? 0} in production`} icon={Boxes} tone="green" />
        <Kpi label="Candidates under review" value={stats.candidates ?? 0} hint="tested before promotion" icon={FlaskConical} tone="amber" />
        <Kpi label="Loads in 30 days" value={stats.loads_30d ?? 0} hint="skill loads by runs" icon={TrendingUp} tone="blue" />
        <Kpi label="This skill" value={card ? `v${card.production?.version ?? "–"}` : "–"} hint={card ? `${card.loads_30d} loads · ${card.runs_30d} runs in 30 days` : ""} icon={Sparkles} tone="purple" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Skill configuration" subtitle={after ? `Production v${prod?.version} (rev ${prod?.revision}) against v${after.version} (rev ${after.revision}, ${pretty(after.status)})` : `Production v${prod?.version ?? "–"}: the only version`}
                action={diff?.data ? <Tag tone="blue"><GitCompareArrows className="h-3 w-3" /> +{diff.data.added} −{diff.data.removed}</Tag> : undefined}>
            {diff?.data?.files.length ? (
              <div className="flex flex-col gap-3">
                {diff.data.files.map((f) => (
                  <div key={f.path}>
                    <div className="os-muted mb-1 flex items-center justify-between text-[11px] font-bold"><span className="font-mono">{f.path}</span><span>{pretty(f.status)} · +{f.added} −{f.removed}</span></div>
                    <div className="os-code max-h-[340px]">
                      {f.ops.map((op, i) => {
                        const [kind, , , text] = op;
                        if (kind === "…") return <div key={i} className="cmt">··· {text} unchanged lines</div>;
                        return <div key={i} className={cn(kind === "+" && "add", kind === "-" && "del")}>{kind === " " ? "  " : `${kind} `}{String(text)}</div>;
                      })}
                    </div>
                  </div>
                ))}
              </div>
            ) : skillMd ? (
              <div className="os-code max-h-[420px] whitespace-pre-wrap">{skillMd.slice(0, 4000)}</div>
            ) : <Empty icon={Sparkles} title="Pick a skill" />}
            {after?.change_note && <div className="os-subtle mt-3 rounded-lg p-2.5 text-[12px]"><b>Change note.</b> {after.change_note} <span className="os-faint">· {after.created_by}, {ago(after.created_at)}</span></div>}
          </Card>

          <Card title="Versioned skills and connectors" subtitle="Every skill the agents load, with the version each stage pins" pad={false}>
            <div className="max-h-[480px] overflow-auto">
              <table className="os-table">
                <thead className="sticky top-0"><tr><th>Skill</th><th>Stages</th><th>Production</th><th>Candidate</th><th className="text-right">Loads 30d</th></tr></thead>
                <tbody>
                  {[...list].sort((a, b) => b.loads_30d - a.loads_30d).map((s) => (
                    <tr key={s.skill_name} className={cn(s.skill_name === name && "bg-[var(--os-hover)]")}>
                      <td><Link href={`/os/skills?skill=${encodeURIComponent(s.skill_name)}`} className="font-bold hover:underline">{s.skill_name.toLowerCase()}</Link>
                        <div className="os-faint text-[10.5px]">{pretty(s.skill_type)} · {pretty(s.origin)}</div></td>
                      <td className="max-w-[200px]"><div className="flex flex-wrap gap-1">{s.stages.filter((x) => x.enabled).map((x) => <Tag key={x.stage}>{pretty(x.stage)}</Tag>)}</div></td>
                      <td>{s.production ? <Tag tone="green">v{s.production.version}</Tag> : <span className="os-faint">–</span>}</td>
                      <td>{s.candidate && s.candidate.skill_id !== s.production?.skill_id ? <Tag tone="amber">v{s.candidate.version}</Tag> : <span className="os-faint">–</span>}</td>
                      <td className="text-right tabular-nums">{s.loads_30d}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Skill test bench" subtitle="The candidate against the baseline on the skill's evaluation set" action={name ? <Action access={access} id="editSkill" label="Run tests" href={`/os/skills/registry/${encodeURIComponent(name)}`} /> : undefined}>
            {testRun ? (
              <>
                {[["Baseline", rate(testRun.baseline_rate), testRun.baseline_passed], ["Candidate", rate(testRun.candidate_rate), testRun.candidate_passed]].map(([l, r, p]) => (
                  <div key={String(l)} className="mb-3">
                    <div className="flex items-center justify-between text-[12px]"><b>{l}</b><span className="text-[18px] font-extrabold tabular-nums">{r}%</span></div>
                    <Bar value={Number(r)} tone={Number(r) >= 80 ? "green" : "amber"} />
                    <div className="os-faint mt-0.5 text-[10.5px]">{p} of {testRun.total} cases pass</div>
                  </div>
                ))}
                <div className="os-divide">
                  {testRun.rows.slice(0, 4).map((row) => (
                    <ActivityRow key={row.index} tone={row.candidate_pass ? "green" : "red"} title={row.title} sub={row.reason} />
                  ))}
                </div>
              </>
            ) : (
              <Empty icon={FlaskConical} title="No evaluation recorded for this skill">
                Run the test bench from the skills registry before a candidate is promoted; results appear here.
              </Empty>
            )}
          </Card>

          <Card title="Version history" subtitle="Every version is kept; a rollback is a label move" pad={false}>
            {versions.length ? (
              <div className="os-divide">
                {versions.map((v) => (
                  <div key={v.skill_id} className="flex items-center justify-between gap-2 px-5 py-2.5">
                    <span className="min-w-0"><span className="block text-[12.5px] font-bold">v{v.version} <span className="os-faint font-semibold">rev {v.revision}</span></span>
                      <span className="os-faint block truncate text-[10.5px]">{v.change_note ?? pretty(v.origin)} · {v.created_by} · {ago(v.created_at)}</span></span>
                    <span className="flex shrink-0 flex-wrap items-start justify-end gap-1">
                      {v.labels.map((l) => <Tag key={l} tone={l === "production" ? "green" : l === "candidate" ? "amber" : "blue"}>{l}</Tag>)}
                      {!v.labels.length && <Tag tone={v.status === "RETIRED" ? "neutral" : "blue"}>{pretty(v.status)}</Tag>}
                      {/* a label move in place: candidate (SKILL.EDIT), production (SKILL.RELEASE; on an older version it is the rollback) */}
                      {name && v.status !== "RETIRED" && !v.labels.includes("production") && actMode(access, ACTIONS.releaseSkill.privilege) !== "none" && (
                        <SkillLabel name={name} skillId={v.skill_id} version={v.version} label="production" mode={actMode(access, ACTIONS.releaseSkill.privilege) as "do" | "request"}
                                    previewing={!!access.preview} approver={access.approvers[ACTIONS.releaseSkill.privilege]} />
                      )}
                      {name && v.status !== "RETIRED" && !v.labels.length && actMode(access, ACTIONS.editSkill.privilege) !== "none" && (
                        <SkillLabel name={name} skillId={v.skill_id} version={v.version} label="candidate" mode={actMode(access, ACTIONS.editSkill.privilege) as "do" | "request"}
                                    previewing={!!access.preview} approver={access.approvers[ACTIONS.editSkill.privilege]} />
                      )}
                    </span>
                  </div>
                ))}
              </div>
            ) : <div className="p-5"><Empty icon={History} title="No versions" /></div>}
          </Card>

          <Card title="Controlled recovery" subtitle="Runs to resume once the fix is approved">
            {failing.length ? (
              <div className="os-divide">
                {failing.map((r) => {
                  const href = r.current_state === "VALIDATION_FAILED" ? `/os/review?run=${r.run_id}` : `/os/build?run=${r.run_id}`;
                  return (
                    <div key={r.run_id} className="py-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <Link href={href} className="min-w-0 truncate text-[12px] font-semibold hover:underline">{r.run_name}</Link>
                        <Tag tone={r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED" ? "red" : "green"}>{stateLabel(r.current_state)}</Tag>
                      </div>
                      {canOperate && r.current_state === "VALIDATION_FAILED" && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <BuildEndToEnd runId={r.run_id} previewing={!!access.preview} label="Resume from checkpoint" />
                          <Btn href={`/os/review?run=${encodeURIComponent(r.run_id)}`}>Open validation gate</Btn>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : <div className="os-faint text-[12px]">No run is blocked right now.</div>}
            <div className="os-divide mt-2">
              <ActivityRow tone="green" icon={ShieldCheck} title="Approved steps stay approved" sub="Mapping, transformations and data quality decisions are kept" />
              <ActivityRow tone="blue" icon={RotateCcw} title="Rerun from the checkpoint" sub="Generation and validation rerun with the new skill version pinned" />
              <ActivityRow tone="purple" icon={ArrowRight} title="Independent validation" sub="The reviewer agent must pass the regenerated output" />
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
