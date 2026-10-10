"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import {
  ExternalLink, FolderGit2, GitPullRequest, Loader2, Lock, RefreshCw, Rocket, Settings2,
  ShieldCheck, Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { generateDbt, listDbtBranches, publishDbt } from "../pipeline-actions";
import { repoBranches } from "../../../code/actions";
import type {
  CortexModel, DbtArtifact, DbtGeneration, DbtPublication, DbtRunRepo, DbtSetup, GenerationReport, GitBranch,
} from "./dbt-types";
import { ReviewPanel } from "./review-panel";
import { Callout, Section, StatusPill, StepIcon, ToastProvider, type Tone, useToast } from "./studio-ui";

const snake = (v: string) => v.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
const TOKEN_FAIL = new Set(["TOKEN_SCOPE", "AUTH"]);
const ADMIN_LINK = "/admin?section=integrations";

function friendly(raw: string) {
  if (/too many arguments|expected 1, got 2/i.test(raw)) {
    return "Snowflake still has the old one-argument GENERATE_DBT. Deploy from Admin, then generate again.";
  }
  return raw;
}

type Props = {
  runId: string;
  runName: string;
  domainName?: string | null;
  canGenerate: boolean;
  generation: DbtGeneration | null;
  artifacts: DbtArtifact[];
  branch: Record<string, unknown> | null;
  lastWorkspace?: Record<string, unknown> | null;
  publication?: DbtPublication | null;
  report?: GenerationReport | null;
  skeletonBase?: Record<string, string> | null;
  models: CortexModel[];
  defaultModel: string;
  /** Repository, defaults and publishing readiness, all configured once in Admin, Integrations. */
  setup: DbtSetup;
};

export function DbtStudio(props: Props) {
  return <ToastProvider><Studio {...props} /></ToastProvider>;
}

/** The run's dbt workspace: cut a branch, generate and review with code context, push and open the PR.
 *  Where the code goes (repository, clone, origin, credentials, publishing) is configured in Admin, never here. */
function Studio({
  runId, runName, domainName, canGenerate, generation, artifacts, branch, publication, report, skeletonBase, lastWorkspace,
  models, defaultModel, setup,
}: Props) {
  const toast = useToast();
  const { canAct } = useAccess();
  const mayManage = canAct("INTEGRATION.MANAGE");
  const stored = branch ?? {};

  // ---------------------------------------------------------------- the repository (configured in Admin)
  const storedRepoId = String(stored.code_repo_id ?? "");
  const startRepo = setup.repository ?? setup.candidates.find((c) => c.repo_id === storedRepoId) ?? null;
  const [repoId, setRepoId] = useState(startRepo?.repo_id ?? "");
  const repo: DbtRunRepo | null = setup.candidates.find((c) => c.repo_id === repoId) ?? (setup.repository?.repo_id === repoId ? setup.repository : null);
  const legacy = setup.legacy && !repo; // a clone set up by hand before repositories lived in Admin
  const sameRepoAsStored = repo ? storedRepoId === repo.repo_id : legacy;
  const publishingReady = setup.publishing.ready;
  const githubTarget = repo ? repo.provider === "GITHUB" : legacy && /github\.com\//i.test(String(setup.legacy_setup?.origin ?? ""));

  // ---------------------------------------------------------------- per-run choices
  const [baseBranch, setBaseBranch] = useState(String((sameRepoAsStored && stored.base_branch) || repo?.branch || setup.legacy_setup?.base_branch || "main"));
  const [cutBranch, setCutBranch] = useState(String(stored.cut_branch ?? setup.default_cut_branch));
  const [prefix, setPrefix] = useState(String(stored.prefix ?? report?.prefix ?? ""));
  // Untouched and never stored: let the run's modeling standard choose (GDP for GDP runs, none otherwise).
  const [prefixChosen, setPrefixChosen] = useState(stored.prefix != null || report?.prefix != null);
  const [sourceKey, setSourceKey] = useState(String(stored.source_key ?? report?.source_key ?? ""));
  const [domainFolder, setDomainFolder] = useState(String(stored.domain_folder ?? report?.domain ?? snake(domainName || "")));
  const [fetchSkeleton, setFetchSkeleton] = useState(stored.fetch_skeleton !== false);
  const [openPr, setOpenPr] = useState(Boolean(stored.push ?? repo?.open_pr ?? true));
  const [draftPr, setDraftPr] = useState(Boolean(repo?.draft_pr ?? false));
  const [dbtProject, setDbtProject] = useState(String(stored.dbt_project ?? ""));
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [branchNote, setBranchNote] = useState("");
  const [listing, setListing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastPublish, setLastPublish] = useState<{ status: string; detail?: string } | null>(null);
  const [, start] = useTransition();

  const canPush = publishingReady && githubTarget;
  const push = openPr && canPush;
  const sameBranch = baseBranch.trim() !== "" && baseBranch.trim() === cutBranch.trim();
  const baseMissing = branches.length > 0 && !branches.some((b) => b.name === baseBranch);
  const branchExists = branches.some((b) => b.name === cutBranch.trim());
  const pub = publication;
  const pubStatus = lastPublish?.status ?? pub?.status;
  const pubDetail = lastPublish?.detail ?? pub?.detail ?? "";
  const published = pubStatus === "PUBLISHED" || pubStatus === "NO_CHANGES";
  const tokenProblem = Boolean(pubStatus && TOKEN_FAIL.has(pubStatus));
  const skeleton = (lastWorkspace?.lineage as { skeleton?: { files?: number; error?: string } } | undefined)?.skeleton;
  const needsPick = !repo && !legacy && setup.candidates.length > 1;
  const folder = repo?.dbt_project_dir || "";

  // ---------------------------------------------------------------- branches of the repository
  const loadBranches = (fetch = true) => {
    const clone = repo ? null : legacy ? String(setup.legacy_setup?.git_repository ?? "") : "";
    if (!repo && !clone) { setBranches([]); return; }
    setListing(true);
    start(async () => {
      if (repo) {
        const res = await repoBranches(repo.repo_id, fetch);
        setListing(false);
        if (!res.ok) { setBranchNote(res.error); return; }
        setBranches(res.data.branches);
        setBranchNote(res.data.error ? `Could not fetch; showing the last fetched branches (${res.data.error.slice(0, 160)})` : `${res.data.branches.length} branches`);
      } else {
        const res = await listDbtBranches(runId, clone as string, fetch);
        setListing(false);
        if (!res.ok) { setBranchNote(res.error); return; }
        setBranches(res.data.branches);
        setBranchNote(res.data.grant_sql ? `Your role cannot use this clone's integration: ${res.data.grant_sql}` : `${res.data.branches.length} branches`);
      }
    });
  };
  useEffect(() => { loadBranches(true); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [repoId]);

  const chooseRepo = (id: string) => {
    const next = setup.candidates.find((c) => c.repo_id === id);
    setRepoId(id);
    if (next) {
      setBaseBranch(next.branch);
      setOpenPr(next.open_pr);
      setDraftPr(next.draft_pr);
    }
  };

  // ---------------------------------------------------------------- step status
  const branchTone: Tone = needsPick ? "active" : sameBranch || baseMissing ? "fail" : cutBranch.trim() && baseBranch.trim() ? "done" : "active";
  const generateTone: Tone = generation ? (report?.anomalies?.length || report?.todos ? "warn" : "done") : "active";
  const publishTone: Tone = published ? "done" : pubStatus && pubStatus !== "NOT_REQUESTED" ? "fail" : generation && canPush ? "active" : "idle";
  const [openStep, setOpenStep] = useState<string>(!generation ? "branch" : "review");
  const toggle = (id: string) => setOpenStep((s) => (s === id ? "" : id));

  // ---------------------------------------------------------------- actions
  const runGenerate = (doPush: boolean) => {
    if (needsPick) { toast("warn", "Pick the repository for this run first."); return; }
    if (sameBranch) { toast("fail", "The new branch must differ from the cut-from branch."); return; }
    if (baseMissing) { toast("fail", `${baseBranch} is not on the remote; pick another cut-from branch.`); return; }
    setBusy(doPush ? "push" : "generate");
    start(async () => {
      const result = await generateDbt(runId, {
        code_repo_id: repo?.repo_id, base_branch: baseBranch.trim() || undefined, cut_branch: cutBranch.trim() || undefined,
        push: doPush, fetch_skeleton: fetchSkeleton, dbt_project: dbtProject.trim() || undefined,
        prefix: prefixChosen ? prefix.trim().toUpperCase() : undefined, source_key: sourceKey.trim() || undefined,
        domain_folder: domainFolder.trim() || undefined,
      });
      setBusy(null);
      if (!result.ok) { toast("fail", friendly(result.error)); return; }
      setLastPublish(null);
      setOpenStep(doPush ? "publish" : "review");
      toast("done", doPush ? "Generated. Publishing to GitHub, see step 3." : "Generated. Review the files below.");
    });
  };

  const runPublish = () => {
    setBusy("publish");
    start(async () => {
      const result = await publishDbt(runId, { base_branch: baseBranch.trim(), cut_branch: cutBranch.trim(), draft: draftPr });
      setBusy(null);
      if (!result.ok) { toast("fail", friendly(result.error)); return; }
      setLastPublish({ status: result.data.status, detail: result.data.detail });
      if (result.data.pull_request?.url) toast("done", `Pull request ready: ${result.data.pull_request.url}`);
      else if (result.data.status === "NO_CHANGES") toast("done", "Branch is up to date; nothing changed vs. the base branch.");
      else toast("fail", result.data.detail || result.data.status);
    });
  };

  // ---------------------------------------------------------------- header CTA
  const cta = !generation
    ? { label: push ? "Generate & open PR" : "Generate", icon: Sparkles, onClick: () => runGenerate(push), busy: busy === "generate" || busy === "push" }
    : published && pub?.pr_url
      ? { label: `Open PR #${pub.pr_number}`, icon: ExternalLink, href: pub.pr_url }
      : canPush
        ? { label: `Push v${generation.generation_version} & open PR`, icon: GitPullRequest, onClick: runPublish, busy: busy === "publish" }
        : null;

  const steps: { id: string; title: string; tone: Tone }[] = [
    { id: "branch", title: "Branch", tone: branchTone },
    { id: "review", title: "Generate & review", tone: generateTone },
    { id: "publish", title: "Publish", tone: publishTone },
  ];

  const where = folder ? `${baseBranch}/${folder}` : baseBranch;
  const timeline: { title: string; detail: string; tone: Tone; href?: string }[] = [
    { title: "Read approved STTM", detail: "DBT-ONBOARD-SOURCE rules engine", tone: generation ? "done" : "idle" },
    {
      title: `Read skeleton from ${where}`,
      detail: !generation ? (fetchSkeleton ? "Every file on the cut-from branch is kept as is" : "Off: models only")
        : skeleton?.error ? skeleton.error : `${report?.skeleton_files ?? skeleton?.files ?? 0} files from ${where}`,
      tone: !generation ? "idle" : skeleton?.error ? "fail" : (report?.skeleton_files ?? skeleton?.files) ? "done" : "warn",
    },
    { title: "Write compile-only project", detail: generation ? `${generation.files_generated} files · ${generation.stage_path}` : "@CODEGEN.DBT_STAGE",
      tone: generation ? "done" : "idle" },
    {
      title: `Push ${pub?.head_branch || cutBranch}`,
      detail: pubStatus ? (published ? `${pub?.files_pushed ?? "?"} files${pub?.commit_sha ? ` · ${pub.commit_sha.slice(0, 7)}` : ""}` : pubDetail || pubStatus)
        : canPush ? `GitHub${folder ? `, under ${folder}/` : ""}` : !githubTarget ? "Stage only (not a GitHub repository)" : "GitHub publishing is not set up",
      tone: published ? "done" : pubStatus && pubStatus !== "NOT_REQUESTED" ? "fail" : "idle",
    },
    { title: "Pull request", detail: pub?.pr_url ? `#${pub.pr_number}` : pubStatus === "NO_CHANGES" ? "No changes vs. base" : `Into ${baseBranch}`,
      tone: pub?.pr_url ? "done" : pubStatus && !published ? "fail" : "idle", href: pub?.pr_url ?? undefined },
    { title: "dbt project in Snowflake", detail: pub?.dbt_project ? `${pub.dbt_project} (from branch)` : "WRITEBACK=FALSE, created from the pushed branch",
      tone: pub?.dbt_project ? "done" : "idle" },
  ];
  const pathPreview = `models/silver/${domainFolder || "<domain>"}/${sourceKey || "<source>"}/${sourceKey || "<source>"}_${report?.target || "<target>"}.sql`;
  const adminHint = (text: string) => mayManage
    ? <Link href={ADMIN_LINK} className="inline-flex items-center gap-1 text-blue-300 underline hover:text-white"><Settings2 className="h-3 w-3" />{text}</Link>
    : <span>{text}</span>;

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------------------------ header */}
      <div className="rounded-xl border bg-gradient-to-r from-slate-900 to-slate-800 p-5 text-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wider text-slate-300">dbt workspace</p>
            <h2 className="mt-1 text-xl font-semibold">{runName}{report?.target ? ` → ${report.target}` : ""}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300">
              {repo ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2 py-0.5">
                  <FolderGit2 className="h-3.5 w-3.5" />{repo.name}
                  <span className="text-slate-400">· {repo.git_url.replace(/^https:\/\//, "")}{folder ? ` · ${folder}/` : ""}</span>
                </span>
              ) : legacy ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-400/20 px-2 py-0.5 text-amber-100"><FolderGit2 className="h-3.5 w-3.5" />clone set up by hand: {String(setup.legacy_setup?.git_repository ?? "")}</span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2 py-0.5"><FolderGit2 className="h-3.5 w-3.5" />no repository: stage only</span>
              )}
              <span className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2 py-0.5"><ShieldCheck className="h-3.5 w-3.5" /> DBT-ONBOARD-SOURCE</span>
              {generation && <span className="rounded-full bg-white/10 px-2 py-0.5">v{generation.generation_version} · {generation.files_generated} files</span>}
              {domainName && <span className="rounded-full bg-white/10 px-2 py-0.5">Domain {domainName}</span>}
              <span className="text-slate-400">{adminHint("Managed in Admin")}</span>
            </div>
          </div>
          {canGenerate && cta && (
            "href" in cta && cta.href ? (
              <a href={cta.href} target="_blank" rel="noreferrer"
                className="inline-flex h-10 items-center gap-2 rounded-md bg-emerald-500 px-5 text-sm font-medium text-white hover:bg-emerald-400">
                <cta.icon className="h-4 w-4" /> {cta.label}
              </a>
            ) : (
              <Button size="lg" className="bg-white text-slate-900 hover:bg-slate-100" onClick={"onClick" in cta ? cta.onClick : undefined}
                disabled={Boolean("busy" in cta && cta.busy) || busy !== null || needsPick}>
                {"busy" in cta && cta.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <cta.icon className="h-4 w-4" />} {cta.label}
              </Button>
            )
          )}
        </div>
        <ol className="mt-5 grid gap-2 sm:grid-cols-3">
          {steps.map((s, i) => (
            <li key={s.id}>
              <button type="button" onClick={() => { setOpenStep(s.id); document.getElementById(`dbt-${s.id}`)?.scrollIntoView({ behavior: "smooth" }); }}
                className={cn("flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                  openStep === s.id ? "bg-white text-slate-900" : "bg-white/5 hover:bg-white/10")}>
                <StepIcon tone={s.tone} n={i + 1} />
                <span className="font-medium">{s.title}</span>
              </button>
            </li>
          ))}
        </ol>
      </div>

      {/* ------------------------------------------------------------------ 1. branch */}
      <Section id="dbt-branch" n={1} title="Branch" tone={branchTone} open={openStep === "branch"} onToggle={() => toggle("branch")}
        subtitle="Cut a new branch from the repository's base branch. The repository itself is configured once in Admin, Integrations."
        summary={<>{repo?.name ?? (legacy ? "set up by hand" : "stage only")} · {cutBranch} from {baseBranch}</>}>
        {needsPick && (
          <div className="mb-4 space-y-1.5">
            <Label htmlFor="repo_pick" className="mt-0">Several repositories serve {domainName || "this domain"}; pick the one for this run</Label>
            <Select id="repo_pick" value={repoId} onChange={(e) => chooseRepo(e.target.value)}>
              <option value="">Choose a repository…</option>
              {setup.candidates.map((c) => <option key={c.repo_id} value={c.repo_id}>{c.name} ({c.git_url.replace(/^https:\/\//, "")})</option>)}
            </Select>
          </div>
        )}
        {setup.candidates.length > 1 && repo && (
          <p className="mb-3 text-xs text-muted-foreground">Repository:
            <Select value={repoId} onChange={(e) => chooseRepo(e.target.value)} className="ml-2 inline-flex h-8 w-auto text-xs" aria-label="Repository">
              {setup.candidates.map((c) => <option key={c.repo_id} value={c.repo_id}>{c.name}</option>)}
            </Select>
          </p>
        )}
        {legacy && (
          <div className="mb-4">
            <Callout tone="warn" title="This run used a clone set up by hand"
              action={setup.candidates.length ? (
                <Button size="sm" variant="outline" onClick={() => chooseRepo(setup.candidates[0].repo_id)}>Use {setup.candidates[0].name}</Button>
              ) : undefined}>
              {String(setup.legacy_setup?.git_repository ?? "")}{setup.legacy_setup?.origin ? ` (${setup.legacy_setup.origin})` : ""}. It keeps working;
              new runs use the repository configured in Admin, Integrations.
            </Callout>
          </div>
        )}
        {!repo && !legacy && !setup.candidates.length && (
          <div className="mb-4">
            <Callout tone="warn" title={`No dbt repository serves ${domainName || "this domain"}`}>
              Generation still works and writes to the Snowflake stage only. To build on the client&apos;s branch and open a PR, an admin connects the
              repository once in {mayManage ? <Link href={ADMIN_LINK} className="underline">Admin, Integrations</Link> : "Admin, Integrations"}.
            </Callout>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <div className="space-y-1">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="base">Cut from</Label>
                {branches.length > 0 ? (
                  <Select id="base" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} aria-invalid={baseMissing}>
                    {baseMissing && <option value={baseBranch}>{baseBranch} (not on the remote)</option>}
                    {branches.map((b) => <option key={b.name} value={b.name}>{b.name}{repo && b.name === repo.branch ? " · default" : ""}</option>)}
                  </Select>
                ) : <Input id="base" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} placeholder="main" className="font-mono" />}
              </div>
              <div>
                <Label htmlFor="cut">New branch</Label>
                <Input id="cut" value={cutBranch} onChange={(e) => setCutBranch(e.target.value)} className={cn("font-mono", sameBranch && "border-red-400")} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 pt-2 text-xs">
              {(repo || legacy) && (
                <Button type="button" size="sm" variant="outline" disabled={listing} onClick={() => loadBranches(true)}>
                  {listing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Fetch branches
                </Button>
              )}
              <span className="text-muted-foreground">{branchNote}</span>
            </div>
            {baseMissing && <p className="text-xs text-red-600">{baseBranch} is no longer on the remote. Pick another cut-from branch.</p>}
            {branchExists && <p className="text-xs text-muted-foreground">{cutBranch} already exists: publishing adds a commit to it and reuses its open PR.</p>}
            {sameBranch && <p className="text-xs text-red-600">The new branch must differ from the cut-from branch.</p>}
            <div className="flex flex-wrap gap-4 pt-3 text-sm">
              <label className={cn("flex items-center gap-2", !canPush && "opacity-60")} title={!canPush ? (githubTarget ? "GitHub publishing is not set up" : "Not a GitHub repository") : undefined}>
                <input type="checkbox" checked={push} disabled={!canPush} onChange={(e) => setOpenPr(e.target.checked)} /> Open the PR after generating
              </label>
              {canPush && <label className="flex items-center gap-2"><input type="checkbox" checked={draftPr} onChange={(e) => setDraftPr(e.target.checked)} /> Draft PR</label>}
            </div>
          </div>
          <details className="rounded-lg border p-3 text-sm" open={Boolean(stored.prefix || stored.source_key || stored.domain_folder)}>
            <summary className="cursor-pointer font-medium">Advanced: naming and skeleton</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="prefix" className="mt-0">{"{PREFIX}"}</Label>
                <Input id="prefix" value={prefix} maxLength={16} onChange={(e) => { setPrefixChosen(true); setPrefix(e.target.value.replace(/[^A-Za-z0-9]/g, "").toUpperCase()); }} placeholder="From the run's standard" className="font-mono" />
              </div>
              <div>
                <Label htmlFor="skey" className="mt-0">Source key</Label>
                <Input id="skey" value={sourceKey} onChange={(e) => setSourceKey(snake(e.target.value))} placeholder="auto (source system)" className="font-mono" />
              </div>
              <div>
                <Label htmlFor="dom" className="mt-0">Domain folder</Label>
                <Input id="dom" value={domainFolder} onChange={(e) => setDomainFolder(snake(e.target.value))} placeholder="auto (domain)" className="font-mono" />
              </div>
            </div>
            <div className="mt-3 rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-5 text-slate-600">
              {folder && <p className="text-slate-400">inside {folder}/</p>}
              <p>models/bronze/{report?.target || "<target>"}_{sourceKey || "<source>"}_source.yml</p>
              <p className="text-slate-900">{pathPreview}</p>
              <p>models/silver/{domainFolder || "<domain>"}/{report?.target || "<target>"}.sql <span className="text-slate-400">(hub; patched if on the branch)</span></p>
              <p>macros/{domainFolder || "<domain>"}_utils.sql</p>
              <p className="mt-1 text-slate-400">audit: {prefix ? `${prefix}_IS_ACTIVE, ${prefix}_INSERTED_TS, ${prefix}_UPDATED_TS …` : "no prefix"}</p>
            </div>
            <label className="mt-3 flex items-center gap-2"><input type="checkbox" checked={fetchSkeleton} onChange={(e) => setFetchSkeleton(e.target.checked)} /> Build on the branch skeleton (keep every file already on {baseBranch})</label>
            <Label htmlFor="dbt_proj">dbt project object (optional)</Label>
            <Input id="dbt_proj" value={dbtProject} onChange={(e) => setDbtProject(e.target.value)} placeholder="Leave empty to auto-name a compile project for this run" className="text-xs" />
          </details>
        </div>
        {canGenerate && (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-4">
            <Button onClick={() => runGenerate(push)} disabled={busy !== null || sameBranch || needsPick || baseMissing}>
              {(busy === "generate" || busy === "push") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {generation ? "Regenerate" : "Generate"}{push ? " & open PR" : ""}
            </Button>
            {push && <Button variant="ghost" onClick={() => runGenerate(false)} disabled={busy !== null || needsPick}>Generate only (review first)</Button>}
          </div>
        )}
      </Section>

      {/* ------------------------------------------------------------------ 2. review */}
      <Section id="dbt-review" n={2} title="Generate & review" tone={generateTone} open={openStep === "review"} onToggle={() => toggle("review")}
        subtitle="Every file the skill produced, with the per-column rule report, a diff against the branch, and a Cortex review that compares with the client's code."
        summary={generation ? <>v{generation.generation_version} · {report?.todos ?? 0} TODO · {report?.anomalies?.length ?? 0} notes</> : "not generated"}>
        <ReviewPanel runId={runId} artifacts={artifacts} report={report ?? null} skeletonBase={skeletonBase ?? null}
          models={models} defaultModel={defaultModel} canEdit={canGenerate} />
      </Section>

      {/* ------------------------------------------------------------------ 3. publish */}
      <Section id="dbt-publish" n={3} title="Publish" tone={publishTone} open={openStep === "publish"} onToggle={() => toggle("publish")}
        subtitle="Push the new branch on top of the cut-from branch, open the pull request, and create the dbt project from that branch."
        summary={pub?.pr_url ? <>PR #{pub.pr_number} · {pub.head_branch}</> : pubStatus || (canPush ? "not published" : "stage only")}>
        <ol className="relative space-y-3 border-l pl-6">
          {timeline.map((t) => (
            <li key={t.title} className="relative">
              <span className="absolute -left-[33px] top-0 rounded-full bg-card"><StepIcon tone={t.tone} n={0} /></span>
              <p className="text-sm font-medium">{t.title}</p>
              {t.href
                ? <a href={t.href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-blue-700 underline">{t.detail} <ExternalLink className="h-3 w-3" /></a>
                : <p className="break-words text-xs text-muted-foreground">{t.detail}</p>}
            </li>
          ))}
        </ol>
        {pubStatus && !published && !tokenProblem && pubStatus !== "NOT_REQUESTED" && (
          <div className="mt-3"><Callout tone="fail" title={`Publish ${pubStatus.toLowerCase().replace("_", " ")}`}>{pubDetail}</Callout></div>
        )}
        {tokenProblem && (
          <div className="mt-3">
            <Callout tone="fail" title="GitHub refused the publishing token for this repository">
              <p className="mb-1 break-words">{pubDetail.split(". Edit the token")[0]}</p>
              The token needs Contents and Pull requests write access to {repo?.git_url.replace("https://github.com/", "") || "the repository"}.
              {" "}{mayManage ? <Link href={ADMIN_LINK} className="underline">Replace it in Admin, Integrations</Link> : "Ask an admin to replace it in Admin, Integrations"}, then push again.
            </Callout>
          </div>
        )}
        {generation && !canPush && (
          <div className="mt-3">
            <Callout tone="warn" title={!githubTarget ? "Stage only" : "GitHub publishing is not set up"}>
              {!githubTarget
                ? (repo ? `${repo.name} is not on GitHub; pull requests from the platform are GitHub only for now. The generated project is in the Snowflake stage.`
                  : "This run has no repository, so the generated project stays in the Snowflake stage.")
                : <>An admin sets up GitHub publishing once in {mayManage ? <Link href={ADMIN_LINK} className="underline">Admin, Integrations</Link> : "Admin, Integrations"}; every run then pushes and opens PRs.</>}
            </Callout>
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
          {pub?.pr_url && (
            <a href={pub.pr_url} target="_blank" rel="noreferrer"
              className="inline-flex h-9 items-center gap-2 rounded-md bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-500">
              <GitPullRequest className="h-4 w-4" /> Open PR #{pub.pr_number}
            </a>
          )}
          {generation && canPush && canGenerate && (
            <>
              <Button variant={pub?.pr_url ? "outline" : "default"} onClick={runPublish} disabled={busy !== null || sameBranch}>
                {busy === "publish" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
                {published ? "Push again & update PR" : `Push v${generation.generation_version} & open PR`}
              </Button>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draftPr} onChange={(e) => setDraftPr(e.target.checked)} /> Draft</label>
            </>
          )}
          {!generation && <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Lock className="h-3.5 w-3.5" />Generate first.</p>}
          {repo && <StatusPill tone={canPush ? "done" : "idle"}>{canPush ? `pushes to ${repo.name}${folder ? `/${folder}` : ""}` : "stage only"}</StatusPill>}
        </div>
      </Section>
    </div>
  );
}
