"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition } from "react";
import {
  CheckCircle2, ExternalLink, GitBranch as GitBranchIcon, GitPullRequest, KeyRound, Loader2, Lock, Plus, RefreshCw,
  Rocket, Settings2, ShieldCheck, Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  checkGithub, createGitRepository, generateDbt, listDbtBranches, publishDbt, rotateGithubToken, setupGithubPublishing,
} from "../pipeline-actions";
import type {
  CortexModel, DbtArtifact, DbtGeneration, DbtPublication, DbtRepo, DbtWorkspace, GenerationReport, GitBranch,
  GithubCheck, GithubStatus,
} from "./dbt-types";
import type { CodeRepo } from "../../../code/actions";
import { ReviewPanel } from "./review-panel";
import { Callout, CopyButton, Section, StatusPill, StepIcon, ToastProvider, type Tone, useToast } from "./studio-ui";

const sameName = (a?: string | null, b?: string | null) => (a || "").toUpperCase() === (b || "").toUpperCase();
const snake = (v: string) => v.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
const TOKEN_FAIL = new Set(["TOKEN_SCOPE", "AUTH"]);

function originOk(origin: string, prefixes: string[]) {
  const url = origin.trim().toLowerCase();
  if (!url) return prefixes.length === 0;
  if (!prefixes.length) return true;
  return prefixes.some((p) => url.startsWith(p.trim().toLowerCase().replace(/\/$/, "")));
}

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
  appliedSkills?: { name: string; version?: string; description?: string }[];
  lastWorkspace?: Record<string, unknown> | null;
  workspace?: DbtWorkspace;
  publication?: DbtPublication | null;
  github?: GithubStatus;
  report?: GenerationReport | null;
  skeletonBase?: Record<string, string> | null;
  /** Repositories configured once in Admin, Integrations, that serve this run's domain. */
  configuredRepos?: CodeRepo[];
};

export function DbtStudio(props: Props) {
  return <ToastProvider><Studio {...props} /></ToastProvider>;
}

function Studio({
  runId, runName, domainName, canGenerate, generation, artifacts, branch, workspace, publication, github, report,
  skeletonBase, lastWorkspace, configuredRepos,
}: Props) {
  const toast = useToast();
  const slug = runName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "run";
  const allRepos = workspace?.git_repositories ?? [];
  const integrations = (workspace?.integrations ?? []).filter((i) => !i.provider || i.provider === "GIT_HTTPS_API");
  const projects = workspace?.dbt_projects ?? [];
  const role = workspace?.role || "";
  const publisherReady = Boolean(github?.ready || workspace?.capabilities?.github_publish);
  const models: CortexModel[] = workspace?.models ?? [];

  // ---------------------------------------------------------------- defaults: saved plan → usable repo → its integration
  const savedInt = integrations.find((i) => sameName(i.name, String(branch?.api_integration ?? "")) && i.usable !== false);
  const firstUsableRepo = allRepos.find((r) => r.usable !== false);
  const startInt = savedInt?.name ?? firstUsableRepo?.api_integration ?? integrations.find((i) => i.usable !== false)?.name
    ?? integrations[0]?.name ?? "";
  const reposForStart = allRepos.filter((r) => sameName(r.api_integration, startInt));
  const startRepo = reposForStart.find((r) => sameName(r.fqn, String(branch?.git_repository ?? "")) && r.usable !== false)
    ?? reposForStart.find((r) => r.usable !== false) ?? reposForStart[0];
  const startPrefixes = integrations.find((i) => sameName(i.name, startInt))?.allowed_prefixes ?? [];
  const savedOrigin = String(branch?.origin ?? branch?.repo ?? "");
  // a repository configured in Admin wins: the saved plan's one if it is configured, else the first configured one
  // (unless the saved plan used a clone set up by hand, which then stays the manual choice)
  const configured = configuredRepos ?? [];
  const savedConfigured = configured.find((c) => sameName(c.git_repository, String(branch?.git_repository ?? "")));
  const startConfigured = savedConfigured ?? (branch?.git_repository ? undefined : configured[0]);

  const [repoSource, setRepoSource] = useState<"configured" | "manual">(startConfigured ? "configured" : "manual");
  const [configuredId, setConfiguredId] = useState(startConfigured?.repo_id ?? "");
  const [integration, setIntegration] = useState(startConfigured?.api_integration ?? startInt);
  const [showAllRepos, setShowAllRepos] = useState(false);
  const [gitRepo, setGitRepo] = useState(startConfigured?.git_repository ?? startRepo?.fqn ?? "");
  const [origin, setOrigin] = useState(startConfigured?.git_url ??
    (startRepo?.origin || (savedOrigin && originOk(savedOrigin, startPrefixes) ? savedOrigin : startPrefixes[0] || savedOrigin)),
  );
  const [baseBranch, setBaseBranch] = useState(String(branch?.base_branch ?? startConfigured?.branch ?? "main"));
  const [cutBranch, setCutBranch] = useState(String(branch?.cut_branch ?? `feat/onboard-${slug}`));
  const [dbtProject, setDbtProject] = useState(String(branch?.dbt_project ?? ""));
  const [prefix, setPrefix] = useState(String(branch?.prefix ?? report?.prefix ?? ""));
  // Untouched and never stored: let the run's modeling standard choose (GDP for GDP runs, none otherwise).
  const [prefixChosen, setPrefixChosen] = useState(branch?.prefix != null || report?.prefix != null);
  const [sourceKey, setSourceKey] = useState(String(branch?.source_key ?? report?.source_key ?? ""));
  const [domainFolder, setDomainFolder] = useState(String(branch?.domain_folder ?? report?.domain ?? snake(domainName || "")));
  const [push, setPush] = useState(Boolean(branch?.push ?? true) && publisherReady);
  const [fetchSkeleton, setFetchSkeleton] = useState(branch?.fetch_skeleton !== false);
  const [draftPr, setDraftPr] = useState(false);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [latestBranch, setLatestBranch] = useState("");
  const [fetchNote, setFetchNote] = useState("");
  const [grantSql, setGrantSql] = useState<string | null>(null);
  const [listing, setListing] = useState(false);
  const [newRepoName, setNewRepoName] = useState(
    ((startPrefixes[0] || "").split("/").filter(Boolean).pop() || "").replace(/[^A-Za-z0-9_]/g, "_").toUpperCase(),
  );
  const [token, setToken] = useState("");
  const [rotating, setRotating] = useState(false);
  const [check, setCheck] = useState<GithubCheck | null>(null);
  const [setupLog, setSetupLog] = useState<{ sql: string; ok: boolean; error?: string }[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastPublish, setLastPublish] = useState<{ status: string; detail?: string } | null>(null);
  const [, start] = useTransition();

  const repos = useMemo(
    () => (showAllRepos || !integration ? allRepos : allRepos.filter((r) => sameName(r.api_integration, integration))),
    [allRepos, integration, showAllRepos],
  );
  const selectedRepo: DbtRepo | undefined = allRepos.find((r) => sameName(r.fqn, gitRepo));
  const chosenConfigured = repoSource === "configured" ? configured.find((c) => c.repo_id === configuredId) : undefined;
  const selectedInt = integrations.find((i) => sameName(i.name, integration));
  const prefixes = selectedInt?.allowed_prefixes ?? [];
  const originValid = originOk(origin, prefixes);
  const isGithub = /^(https:\/\/github\.com\/|git@github\.com:)[^/]+\/[^/]+/i.test(origin.trim());
  const sameBranch = baseBranch.trim() !== "" && baseBranch.trim() === cutBranch.trim();
  const branchExists = branches.some((b) => b.name === cutBranch.trim());
  const pub = publication;
  const pubStatus = lastPublish?.status ?? pub?.status;
  const pubDetail = lastPublish?.detail ?? pub?.detail ?? "";
  const published = pubStatus === "PUBLISHED" || pubStatus === "NO_CHANGES";
  const tokenProblem = (pubStatus && TOKEN_FAIL.has(pubStatus)) || (check && TOKEN_FAIL.has(check.status));
  const skeleton = (lastWorkspace?.lineage as { skeleton?: { files?: number; error?: string } } | undefined)?.skeleton;

  // ---------------------------------------------------------------- step status
  const connectTone: Tone = !integration ? "active"
    : selectedRepo?.usable === false || grantSql ? "warn"
      : (gitRepo || !fetchSkeleton) && (publisherReady || !push) && !tokenProblem ? "done" : "warn";
  const configureTone: Tone = sameBranch || !originValid ? "fail" : baseBranch && cutBranch ? "done" : "active";
  const generateTone: Tone = generation ? (report?.anomalies?.length || report?.todos ? "warn" : "done") : "active";
  const publishTone: Tone = published ? "done" : pubStatus && pubStatus !== "NOT_REQUESTED" ? "fail" : generation ? "active" : "idle";
  const [openStep, setOpenStep] = useState<string>(!generation ? (connectTone === "done" ? "configure" : "connect") : "review");
  const toggle = (id: string) => setOpenStep((s) => (s === id ? "" : id));

  // ---------------------------------------------------------------- actions
  const chooseIntegration = (name: string) => {
    setIntegration(name);
    setShowAllRepos(false);
    const forInt = allRepos.filter((r) => sameName(r.api_integration, name));
    const next = forInt.find((r) => r.usable !== false) ?? forInt[0];
    const intPrefixes = integrations.find((i) => sameName(i.name, name))?.allowed_prefixes ?? [];
    setGitRepo(next?.fqn ?? "");
    setOrigin(next?.origin || intPrefixes[0] || "");
    setGrantSql(null);
    if (!next) {
      setBranches([]);
      setLatestBranch("");
      setFetchNote(intPrefixes.length ? `No Snowflake git clone uses ${name} yet; create one to read branches.` : "");
    }
    setNewRepoName(((intPrefixes[0] || "").split("/").filter(Boolean).pop() || "").replace(/[^A-Za-z0-9_]/g, "_").toUpperCase());
    setCheck(null);
  };

  const chooseConfigured = (repo: CodeRepo) => {
    setRepoSource("configured");
    setConfiguredId(repo.repo_id);
    setIntegration(repo.api_integration ?? "");
    setShowAllRepos(true);
    setGitRepo(repo.git_repository);
    setOrigin(repo.git_url);
    setBaseBranch(repo.branch);
    setGrantSql(null);
    setCheck(null);
  };

  const applyRepo = (fqn: string) => {
    setGitRepo(fqn);
    const next = allRepos.find((r) => r.fqn === fqn);
    if (next?.origin) setOrigin(next.origin);
    if (next?.api_integration && !sameName(next.api_integration, integration)) setIntegration(next.api_integration);
    setCheck(null);
  };

  const refreshBranches = (repo: string, preferLatest = false) => {
    if (!repo.trim()) { setBranches([]); setLatestBranch(""); return; }
    setListing(true);
    setGrantSql(null);
    start(async () => {
      const result = await listDbtBranches(runId, repo.trim(), true);
      setListing(false);
      if (!result.ok) { setFetchNote(result.error); return; }
      setBranches(result.data.branches);
      setLatestBranch(result.data.latest);
      setGrantSql(result.data.grant_sql ?? null);
      setFetchNote(result.data.fetched
        ? `${result.data.branches.length} branch${result.data.branches.length === 1 ? "" : "es"} fetched`
        : result.data.grant_sql ? "FETCH blocked; showing last-fetched branches" : (result.data.fetch_warning || "Listed without a fresh FETCH"));
      // a configured repository's branch is the admin's choice: keep it while it exists
      const keep = repoSource === "configured" && result.data.branches.some((b) => b.name === baseBranch);
      if (!keep && (preferLatest || !baseBranch.trim() || baseBranch === "main" || !result.data.branches.some((b) => b.name === baseBranch))) {
        if (result.data.latest) setBaseBranch(result.data.latest);
      }
    });
  };

  useEffect(() => {
    if (gitRepo.trim()) refreshBranches(gitRepo, !branch?.base_branch && repoSource !== "configured");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload whenever the selected repository changes
  }, [gitRepo]);

  const runCheck = () => {
    if (!isGithub) { toast("warn", "Pick a github.com origin first."); return; }
    setBusy("check");
    start(async () => {
      const result = await checkGithub(origin.trim());
      setBusy(null);
      if (!result.ok) { toast("fail", result.error); return; }
      setCheck(result.data);
      if (result.data.status === "OK") {
        toast(result.data.push === false ? "warn" : "done",
          result.data.push === false ? `Connected to ${result.data.repository}, but your account can't push.` : `Connected to ${result.data.repository}.`);
      } else toast("fail", result.data.detail || result.data.status);
    });
  };

  const runRotate = () => {
    if (!token.trim()) return;
    setBusy("rotate");
    start(async () => {
      const result = await rotateGithubToken(token.trim());
      setBusy(null);
      setToken("");
      if (!result.ok) { toast("fail", result.error); return; }
      setRotating(false);
      setLastPublish(null);
      toast("done", "Token updated in the Snowflake secret. Testing the connection…");
      runCheck();
    });
  };

  const runSetup = () => {
    setBusy("setup");
    start(async () => {
      const result = await setupGithubPublishing(runId, { token: token.trim() || undefined });
      setBusy(null);
      setToken("");
      if (!result.ok) { toast("fail", result.error); return; }
      setSetupLog(result.data.log);
      toast(result.data.ready ? "done" : "fail", result.data.ready ? "GitHub publishing is ready." : (result.data.detail || "Setup did not finish."));
    });
  };

  const runCreateRepo = () => {
    setBusy("repo");
    start(async () => {
      const result = await createGitRepository(runId, {
        name: newRepoName || "DBT_REPO", origin: origin.trim() || prefixes[0] || "", api_integration: integration,
      });
      setBusy(null);
      if (!result.ok) { toast("fail", friendly(result.error)); return; }
      setGitRepo(result.data.git_repository);
      toast("done", `Created ${result.data.git_repository} and fetched it.`);
    });
  };

  const runGenerate = (doPush: boolean) => {
    if (sameBranch) { toast("fail", "The new branch must differ from the cut-from branch."); return; }
    if (origin.trim() && prefixes.length && !originValid) { toast("fail", "Origin is outside the integration's allowed prefixes."); return; }
    if (fetchSkeleton && !gitRepo.trim()) { toast("warn", "Pick or create a Snowflake git clone, or turn the skeleton off."); return; }
    if (doPush && !isGithub) { toast("fail", "Opening a PR needs a github.com origin."); return; }
    setBusy(doPush ? "push" : "generate");
    start(async () => {
      const result = await generateDbt(runId, {
        repo: origin.trim() || gitRepo || undefined, origin: origin.trim() || undefined,
        git_repository: gitRepo.trim() || undefined, api_integration: integration || undefined,
        dbt_project: dbtProject.trim() || undefined, allowed_prefixes: prefixes.length ? prefixes : undefined,
        base_branch: baseBranch.trim(), cut_branch: cutBranch.trim(), push: doPush, fetch_skeleton: fetchSkeleton,
        prefix: prefixChosen ? prefix.trim().toUpperCase() : undefined, source_key: sourceKey.trim() || undefined, domain_folder: domainFolder.trim() || undefined,
      });
      setBusy(null);
      if (!result.ok) { toast("fail", friendly(result.error)); return; }
      setLastPublish(null);
      setOpenStep(doPush ? "publish" : "review");
      toast("done", doPush ? "Generated. Publishing to GitHub — see step 4." : "Generated. Review the files below.");
    });
  };

  const runPublish = () => {
    if (!isGithub) { toast("fail", "Opening a PR needs a github.com origin."); return; }
    setBusy("publish");
    start(async () => {
      const result = await publishDbt(runId, {
        origin: origin.trim(), base_branch: baseBranch.trim(), cut_branch: cutBranch.trim(),
        git_repository: gitRepo.trim() || undefined, draft: draftPr,
      });
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
      : publisherReady
        ? { label: `Push v${generation.generation_version} & open PR`, icon: GitPullRequest, onClick: runPublish, busy: busy === "publish" }
        : { label: "Set up GitHub", icon: KeyRound, onClick: () => setOpenStep("connect") };

  const steps: { id: string; title: string; tone: Tone }[] = [
    { id: "connect", title: "Connect", tone: connectTone },
    { id: "configure", title: "Configure", tone: configureTone },
    { id: "review", title: "Generate & review", tone: generateTone },
    { id: "publish", title: "Publish", tone: publishTone },
  ];

  const timeline: { title: string; detail: string; tone: Tone; href?: string }[] = [
    { title: "Read approved STTM", detail: "DBT-ONBOARD-SOURCE rules engine", tone: generation ? "done" : "idle" },
    {
      title: `Read skeleton from ${baseBranch}`,
      detail: !generation ? (fetchSkeleton ? "Every file on the cut-from branch is kept as-is" : "Off: models only")
        : skeleton?.error ? skeleton.error : `${report?.skeleton_files ?? skeleton?.files ?? 0} files from ${baseBranch}`,
      tone: !generation ? "idle" : skeleton?.error ? "fail" : (report?.skeleton_files ?? skeleton?.files) ? "done" : "warn",
    },
    {
      title: "Write compile-only project",
      detail: generation ? `${generation.files_generated} files · ${generation.stage_path}` : "@CODEGEN.DBT_STAGE",
      tone: generation ? "done" : "idle",
    },
    {
      title: `Push ${pub?.head_branch || cutBranch}`,
      detail: pubStatus ? (published ? `${pub?.files_pushed ?? "?"} files${pub?.commit_sha ? ` · ${pub.commit_sha.slice(0, 7)}` : ""}` : pubDetail || pubStatus)
        : publisherReady ? "GitHub API via CODEGEN.PUBLISH_DBT_PR" : "GitHub publishing not set up",
      tone: published ? "done" : pubStatus && pubStatus !== "NOT_REQUESTED" ? "fail" : "idle",
    },
    {
      title: "Pull request",
      detail: pub?.pr_url ? `#${pub.pr_number}` : pubStatus === "NO_CHANGES" ? "No changes vs. base" : `Into ${baseBranch}`,
      tone: pub?.pr_url ? "done" : pubStatus && !published ? "fail" : "idle", href: pub?.pr_url ?? undefined,
    },
    {
      title: "dbt project in Snowflake",
      detail: pub?.dbt_project ? `${pub.dbt_project} (from branch)` : "WRITEBACK=FALSE, created from the pushed branch",
      tone: pub?.dbt_project ? "done" : "idle",
    },
  ];

  const pathPreview = `models/silver/${domainFolder || "<domain>"}/${sourceKey || "<source>"}/${sourceKey || "<source>"}_${report?.target || "<target>"}.sql`;

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------------------------ header */}
      <div className="rounded-xl border bg-gradient-to-r from-slate-900 to-slate-800 p-5 text-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wider text-slate-300">dbt workspace</p>
            <h2 className="mt-1 text-xl font-semibold">{runName}{report?.target ? ` → ${report.target}` : ""}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300">
              <span className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2 py-0.5"><ShieldCheck className="h-3.5 w-3.5" /> DBT-ONBOARD-SOURCE</span>
              <span className="rounded-full bg-white/10 px-2 py-0.5">bronze → silver staging → silver hub</span>
              {generation && <span className="rounded-full bg-white/10 px-2 py-0.5">v{generation.generation_version} · {generation.files_generated} files</span>}
              {domainName && <span className="rounded-full bg-white/10 px-2 py-0.5">Domain {domainName}</span>}
            </div>
          </div>
          {canGenerate && (
            "href" in cta && cta.href ? (
              <a href={cta.href} target="_blank" rel="noreferrer"
                className="inline-flex h-10 items-center gap-2 rounded-md bg-emerald-500 px-5 text-sm font-medium text-white hover:bg-emerald-400">
                <cta.icon className="h-4 w-4" /> {cta.label}
              </a>
            ) : (
              <Button size="lg" className="bg-white text-slate-900 hover:bg-slate-100" onClick={"onClick" in cta ? cta.onClick : undefined}
                disabled={Boolean("busy" in cta && cta.busy) || busy !== null}>
                {"busy" in cta && cta.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <cta.icon className="h-4 w-4" />} {cta.label}
              </Button>
            )
          )}
        </div>
        <ol className="mt-5 grid gap-2 sm:grid-cols-4">
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

      {/* ------------------------------------------------------------------ 1. connect */}
      <Section id="dbt-connect" n={1} title="Connect" tone={connectTone} open={openStep === "connect"} onToggle={() => toggle("connect")}
        subtitle="Choose the git integration. Its repository clone, allowed origins and branches load automatically."
        summary={<>{chosenConfigured ? `${chosenConfigured.name} (configured)` : `${integration || "no integration"} · ${selectedRepo?.fqn || gitRepo || "no clone"}`} · GitHub {publisherReady ? (tokenProblem ? "needs token fix" : "ready") : "not set up"}</>}>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3 rounded-lg border p-4">
            <p className="flex items-center gap-2 text-sm font-semibold"><GitBranchIcon className="h-4 w-4" /> Snowflake git (read-only skeleton)</p>
            <div className="flex rounded-lg border p-0.5 text-xs">
              {([["configured", `Configured repositories (${configured.length})`], ["manual", "Set up by hand"]] as const).map(([k, l]) => (
                <button key={k} type="button"
                        onClick={() => { if (k === "configured" && configured[0] && !configured.some((c) => c.repo_id === configuredId)) chooseConfigured(configured[0]); else setRepoSource(k); }}
                        className={cn("flex-1 rounded-md px-2 py-1", repoSource === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>{l}</button>
              ))}
            </div>
            {repoSource === "configured" ? (
              configured.length ? (
                <div className="space-y-1.5">
                  {configured.map((c) => {
                    const active = c.repo_id === configuredId;
                    return (
                      <button key={c.repo_id} type="button" onClick={() => chooseConfigured(c)}
                        className={cn("flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left text-xs",
                          active ? "border-blue-500 bg-blue-50/60 ring-1 ring-blue-200" : "hover:bg-slate-50")}>
                        <GitBranchIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{c.name}</span>
                          <span className="block truncate font-mono text-[11px] text-muted-foreground">{c.git_url.replace(/^https:\/\//, "")}</span>
                          <span className="block truncate text-[11px] text-muted-foreground">default branch <span className="font-mono">{c.branch}</span> · clone <span className="font-mono">{c.git_repository}</span></span>
                        </span>
                        {c.status === "FAILED" && <StatusPill tone="warn">index failed</StatusPill>}
                      </button>
                    );
                  })}
                  <p className="text-[11px] text-muted-foreground">The clone, origin and integration come from the configuration; pick the cut-from branch in step 2.
                    {" "}<Link href="/admin?section=integrations" className="inline-flex items-center gap-0.5 text-primary hover:underline"><Settings2 className="h-3 w-3" />Manage in Admin</Link></p>
                  {(selectedRepo?.grant_sql || grantSql) && (
                    <Callout tone="warn" title="Your role can't use this clone's integration" action={<CopyButton text={(grantSql || selectedRepo?.grant_sql) as string} />}>
                      Ask an admin to run <code className="font-mono">{grantSql || selectedRepo?.grant_sql}</code>
                    </Callout>
                  )}
                </div>
              ) : (
                <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">No repository serves this domain yet. An admin can configure it once in
                  {" "}<Link href="/admin?section=integrations" className="text-primary hover:underline">Admin, Integrations</Link>, and every run uses it. Or set it up by hand for this run.</p>
              )
            ) : (
              <>
              {!!workspace?.warnings?.length && (
                <details className="text-[11px] text-muted-foreground">
                  <summary className="cursor-pointer">{workspace.warnings.length} discovery note(s)</summary>
                  <ul className="mt-1 list-inside list-disc">{workspace.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                </details>
              )}
              <div>
                <Label htmlFor="git_int" className="mt-0">API integration</Label>
                <div className="grid gap-1.5">
                  {integrations.map((i) => {
                    const count = allRepos.filter((r) => sameName(r.api_integration, i.name)).length;
                    const active = sameName(i.name, integration);
                    return (
                      <button key={i.name} type="button" onClick={() => chooseIntegration(i.name)}
                        className={cn("flex items-center justify-between rounded-md border px-3 py-2 text-left text-xs",
                          active ? "border-blue-500 bg-blue-50/60 ring-1 ring-blue-200" : "hover:bg-slate-50")}>
                        <span className="min-w-0">
                          <span className="block truncate font-mono font-medium">{i.name}</span>
                          <span className="block truncate text-[11px] text-muted-foreground">{(i.allowed_prefixes ?? []).join(", ") || "no allowed prefixes"}</span>
                        </span>
                        <span className="ml-2 flex shrink-0 items-center gap-1.5">
                          {i.usable === false && <StatusPill tone="warn">no USAGE</StatusPill>}
                          <StatusPill tone={count ? "done" : "idle"}>{count} clone{count === 1 ? "" : "s"}</StatusPill>
                        </span>
                      </button>
                    );
                  })}
                  {!integrations.length && <p className="text-xs text-muted-foreground">No GIT_HTTPS_API integrations are visible to {role || "this role"}.</p>}
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="git_repo">Repository clone</Label>
                  {integration && allRepos.length > repos.length && (
                    <button type="button" className="mt-3 text-[11px] text-muted-foreground underline" onClick={() => setShowAllRepos(true)}>show all {allRepos.length}</button>
                  )}
                </div>
                {repos.length > 0 ? (
                  <Select id="git_repo" value={gitRepo} onChange={(e) => applyRepo(e.target.value)}>
                    <option value="">Select repository</option>
                    {repos.map((r) => <option key={r.fqn} value={r.fqn}>{r.fqn}{r.usable === false ? " · no access" : ""}</option>)}
                  </Select>
                ) : integration ? (
                  <div className="flex gap-2">
                    <Input value={newRepoName} onChange={(e) => setNewRepoName(e.target.value.toUpperCase())} placeholder="DBT_DEMO" className="font-mono text-xs" />
                    <Button type="button" variant="outline" disabled={busy !== null || !(origin.trim() || prefixes[0])} onClick={runCreateRepo}>
                      {busy === "repo" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Create clone
                    </Button>
                  </div>
                ) : <p className="text-xs text-muted-foreground">Pick an integration.</p>}
              </div>
              {(selectedRepo?.grant_sql || grantSql) && (
                <Callout tone="warn" title="Your role can't use this clone's integration" action={<CopyButton text={(grantSql || selectedRepo?.grant_sql) as string} />}>
                  Ask an admin to run <code className="font-mono">{grantSql || selectedRepo?.grant_sql}</code>
                </Callout>
              )}
              <div>
                <Label htmlFor="origin">Origin URL</Label>
                <Input id="origin" value={origin} onChange={(e) => setOrigin(e.target.value)} placeholder={prefixes[0] || "https://github.com/org/repo"}
                  aria-invalid={!originValid} className={cn(!originValid && "border-red-400")} />
                {!originValid && <p className="mt-1 text-xs text-red-600">Must start with an allowed prefix of {integration}.</p>}
                {prefixes.length > 1 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {prefixes.map((p) => (
                      <button key={p} type="button" onClick={() => setOrigin(p)}
                        className={cn("rounded-full border px-2 py-0.5 font-mono text-[11px]", origin.startsWith(p) ? "border-blue-500 bg-blue-50" : "hover:bg-slate-50")}>{p}</button>
                    ))}
                  </div>
                )}
              </div>
              </>
            )}
          </div>

          <div className="space-y-3 rounded-lg border p-4">
            <div className="flex items-center justify-between">
              <p className="flex items-center gap-2 text-sm font-semibold"><GitPullRequest className="h-4 w-4" /> GitHub publishing</p>
              <StatusPill tone={!publisherReady ? "idle" : tokenProblem ? "fail" : check?.status === "OK" && check.push !== false ? "done" : "active"}>
                {!publisherReady ? "not set up" : tokenProblem ? "token needs access" : check?.status === "OK" ? "connected" : "ready"}
              </StatusPill>
            </div>
            {!publisherReady ? (
              <>
                <p className="text-xs text-muted-foreground">
                  Snowflake git clones are read-only, so branches and PRs go through the GitHub API from a Snowflake procedure.
                  The token is stored in a Snowflake secret; this app never keeps it. This one-time setup needs CREATE INTEGRATION
                  and CREATE SECRET.
                </p>
                <TokenHelp repo={origin} />
                <div className="flex gap-2">
                  <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="github_pat_…" />
                  <Button onClick={runSetup} disabled={busy !== null}>{busy === "setup" && <Loader2 className="h-4 w-4 animate-spin" />} Set up</Button>
                </div>
                {setupLog.length > 0 && (
                  <ol className="space-y-1 text-[11px]">
                    {setupLog.map((s) => (
                      <li key={s.sql} className={cn("rounded border px-2 py-1 font-mono", s.ok ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50")}>
                        <span className="block whitespace-pre-wrap break-all">{s.sql}</span>
                        {s.error && <span className="mt-0.5 block font-sans text-red-700">{s.error}</span>}
                      </li>
                    ))}
                  </ol>
                )}
              </>
            ) : (
              <>
                {check?.status === "OK" && (
                  <Callout tone={check.push === false ? "warn" : "done"} title={`Connected to ${check.repository}`}>
                    Default branch {check.default_branch} · {check.private ? "private" : "public"} · push {check.push === false ? "not allowed for your account" : "allowed"}
                  </Callout>
                )}
                {tokenProblem && (
                  <Callout tone="fail" title="GitHub refused the token for this repository">
                    <p className="mb-1 break-words">{(check && TOKEN_FAIL.has(check.status) ? check.detail : pubDetail)?.split(". Edit the token")[0]}</p>
                    <TokenHelp repo={origin} />
                  </Callout>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" onClick={runCheck} disabled={busy !== null || !isGithub}>
                    {busy === "check" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />} Test connection
                  </Button>
                  <Button variant={tokenProblem ? "default" : "ghost"} size="sm" onClick={() => setRotating((r) => !r)}>
                    <KeyRound className="h-4 w-4" /> {tokenProblem ? "Replace token" : "Rotate token"}
                  </Button>
                </div>
                {rotating && (
                  <div className="flex gap-2">
                    <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="new github_pat_…" />
                    <Button size="sm" onClick={runRotate} disabled={busy !== null || !token.trim()}>
                      {busy === "rotate" && <Loader2 className="h-4 w-4 animate-spin" />} Save
                    </Button>
                  </div>
                )}
                {!isGithub && origin && <p className="text-xs text-amber-700">The origin must be a github.com repository to open PRs.</p>}
              </>
            )}
          </div>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ 2. configure */}
      <Section id="dbt-configure" n={2} title="Configure" tone={configureTone} open={openStep === "configure"} onToggle={() => toggle("configure")}
        subtitle="Naming from the skill: audit-column prefix, source key and domain folder decide the file layout."
        summary={<>{cutBranch} from {baseBranch} · prefix {prefix || "none"} · {push ? "GitHub PR" : "stage only"}</>}>
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <div className="space-y-1">
            <p className="text-sm font-semibold">Skill naming</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="prefix">{"{PREFIX}"}</Label>
                <Input id="prefix" value={prefix} maxLength={16} onChange={(e) => { setPrefixChosen(true); setPrefix(e.target.value.replace(/[^A-Za-z0-9]/g, "").toUpperCase()); }} placeholder="Set by the run's standard" className="font-mono" />
              </div>
              <div>
                <Label htmlFor="skey">Source key</Label>
                <Input id="skey" value={sourceKey} onChange={(e) => setSourceKey(snake(e.target.value))} placeholder="auto (source system)" className="font-mono" />
              </div>
              <div>
                <Label htmlFor="dom">Domain folder</Label>
                <Input id="dom" value={domainFolder} onChange={(e) => setDomainFolder(snake(e.target.value))} placeholder="auto (domain)" className="font-mono" />
              </div>
            </div>
            <div className="mt-3 rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-5 text-slate-600">
              <p>models/bronze/{report?.target || "<target>"}_{sourceKey || "<source>"}_source.yml</p>
              <p className="text-slate-900">{pathPreview}</p>
              <p>models/silver/{domainFolder || "<domain>"}/{report?.target || "<target>"}.sql <span className="text-slate-400">(hub; patched if on the branch)</span></p>
              <p>macros/{domainFolder || "<domain>"}_utils.sql</p>
              <p className="mt-1 text-slate-400">audit: {prefix ? `${prefix}_IS_ACTIVE, ${prefix}_INSERTED_TS, ${prefix}_UPDATED_TS …` : "no prefix"}</p>
            </div>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-semibold">Branch</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="base">Cut from</Label>
                {branches.length > 0 ? (
                  <Select id="base" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)}>
                    {branches.map((b) => <option key={b.name} value={b.name}>{b.name}{b.name === latestBranch ? " · latest" : ""}</option>)}
                  </Select>
                ) : <Input id="base" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} placeholder="main" />}
              </div>
              <div>
                <Label htmlFor="cut">New branch</Label>
                <Input id="cut" value={cutBranch} onChange={(e) => setCutBranch(e.target.value)} className={cn("font-mono", sameBranch && "border-red-400")} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 pt-2 text-xs">
              <Button type="button" size="sm" variant="outline" disabled={!gitRepo.trim() || listing} onClick={() => refreshBranches(gitRepo, true)}>
                {listing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Refresh
              </Button>
              {latestBranch && latestBranch !== baseBranch && (
                <button type="button" className="text-blue-700 underline" onClick={() => setBaseBranch(latestBranch)}>use latest ({latestBranch})</button>
              )}
              <span className="text-muted-foreground">{fetchNote}</span>
            </div>
            {branchExists && <p className="text-xs text-muted-foreground">{cutBranch} exists: publishing adds a commit and reuses its open PR.</p>}
            {sameBranch && <p className="text-xs text-red-600">The new branch must differ from the cut-from branch.</p>}
            <div className="mt-3 inline-flex rounded-lg border p-0.5 text-xs">
              <button type="button" disabled={!publisherReady} onClick={() => setPush(true)}
                className={cn("rounded-md px-3 py-1.5", push ? "bg-slate-900 text-white" : "text-slate-600", !publisherReady && "opacity-50")}>
                <GitPullRequest className="mr-1 inline h-3.5 w-3.5" /> GitHub branch + PR
              </button>
              <button type="button" onClick={() => setPush(false)} className={cn("rounded-md px-3 py-1.5", !push ? "bg-slate-900 text-white" : "text-slate-600")}>
                Stage only
              </button>
            </div>
            <div className="flex flex-wrap gap-4 pt-2 text-sm">
              <label className="flex items-center gap-2"><input type="checkbox" checked={fetchSkeleton} onChange={(e) => setFetchSkeleton(e.target.checked)} /> Build on the branch skeleton</label>
              {push && <label className="flex items-center gap-2"><input type="checkbox" checked={draftPr} onChange={(e) => setDraftPr(e.target.checked)} /> Draft PR</label>}
            </div>
            <details className="pt-1 text-xs">
              <summary className="cursor-pointer text-muted-foreground">Advanced</summary>
              <Label htmlFor="dbt_proj">dbt project object (optional)</Label>
              <Input id="dbt_proj" value={dbtProject} onChange={(e) => setDbtProject(e.target.value)} list="dbt_proj_list"
                placeholder={projects[0]?.fqn || "Leave empty to auto-name a compile project for this run"} />
              <datalist id="dbt_proj_list">{projects.map((p) => <option key={p.fqn} value={p.fqn} />)}</datalist>
            </details>
          </div>
        </div>
        {canGenerate && (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-4">
            <Button onClick={() => runGenerate(push)} disabled={busy !== null || sameBranch || !originValid}>
              {(busy === "generate" || busy === "push") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {generation ? "Regenerate" : "Generate"}{push ? " & open PR" : ""}
            </Button>
            {push && <Button variant="ghost" onClick={() => runGenerate(false)} disabled={busy !== null}>Generate only (review first)</Button>}
          </div>
        )}
      </Section>

      {/* ------------------------------------------------------------------ 3. review */}
      <Section id="dbt-review" n={3} title="Generate & review" tone={generateTone} open={openStep === "review"} onToggle={() => toggle("review")}
        subtitle="Every file the skill produced, with the per-column rule report, a diff against the branch, and a Cortex review."
        summary={generation ? <>v{generation.generation_version} · {report?.todos ?? 0} TODO · {report?.anomalies?.length ?? 0} notes</> : "not generated"}>
        <ReviewPanel runId={runId} artifacts={artifacts} report={report ?? null} skeletonBase={skeletonBase ?? null}
          models={models} defaultModel={workspace?.default_model || models[0]?.name || "claude-sonnet-4-5"} canEdit={canGenerate} />
      </Section>

      {/* ------------------------------------------------------------------ 4. publish */}
      <Section id="dbt-publish" n={4} title="Publish" tone={publishTone} open={openStep === "publish"} onToggle={() => toggle("publish")}
        subtitle="Push a new branch on top of the cut-from branch, open the pull request, and create the dbt project from that branch."
        summary={pub?.pr_url ? <>PR #{pub.pr_number} · {pub.head_branch}</> : pubStatus || "not published"}>
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
            <Callout tone="fail" title="Fix the GitHub token, then push again"
              action={<Button size="sm" onClick={() => { setOpenStep("connect"); setRotating(true); }}><KeyRound className="h-4 w-4" /> Replace token</Button>}>
              <TokenHelp repo={origin} />
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
          {generation && publisherReady && canGenerate && (
            <Button variant={pub?.pr_url ? "outline" : "default"} onClick={runPublish} disabled={busy !== null || !isGithub || sameBranch}>
              {busy === "publish" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
              {published ? "Push again & update PR" : `Push v${generation.generation_version} & open PR`}
            </Button>
          )}
          {!publisherReady && <Button variant="outline" onClick={() => setOpenStep("connect")}><Lock className="h-4 w-4" /> Set up GitHub publishing</Button>}
        </div>
      </Section>
    </div>
  );
}

function TokenHelp({ repo }: { repo: string }) {
  const name = repo.replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "") || "your repository";
  return (
    <ol className="list-inside list-decimal space-y-0.5 text-xs">
      <li>
        Open{" "}
        <a className="underline" href="https://github.com/settings/personal-access-tokens" target="_blank" rel="noreferrer">GitHub → Fine-grained tokens</a>
        {" "}and edit (or create) the token.
      </li>
      <li>Repository access: <b>Only select repositories</b> → <span className="font-mono">{name}</span>.</li>
      <li>Repository permissions: <b>Contents: Read and write</b>, <b>Pull requests: Read and write</b> (Metadata is read-only, added automatically).</li>
      <li>If the repo belongs to an organization, the org may need to approve the token.</li>
      <li>Paste the token here with <b>Replace token</b>, then <b>Test connection</b>.</li>
    </ol>
  );
}
