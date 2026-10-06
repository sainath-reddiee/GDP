"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { Copy, ExternalLink, GitBranch as GitBranchIcon, GitPullRequest, Loader2, Lock, Plus, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  applyDbtEnhance, createGitRepository, generateDbt, listDbtBranches, previewDbtEnhance, publishDbt, setupGithubPublishing,
} from "../pipeline-actions";
import type {
  DbtArtifact, CortexModel, DbtGeneration, DbtPublication, DbtRepo, DbtWorkspace, GitBranch, GithubStatus,
} from "./dbt-types";

type StepTone = "done" | "fail" | "skip" | "idle";

function stepTone(tone: StepTone) {
  if (tone === "done") return "border-success/40 bg-success/10";
  if (tone === "fail") return "border-destructive/40 bg-destructive/10";
  if (tone === "skip") return "border-dashed bg-muted/30";
  return "border-border bg-card";
}

function originOk(origin: string, prefixes: string[]) {
  const url = origin.trim().toLowerCase();
  if (!url) return prefixes.length === 0;
  if (!prefixes.length) return true;
  return prefixes.some((p) => url.startsWith(p.trim().toLowerCase().replace(/\/$/, "")));
}

function friendlyError(raw: string) {
  const text = raw || "";
  if (/too many arguments|expected 1, got 2/i.test(text)) {
    return "Snowflake still has the one-argument GENERATE_DBT. The plan was saved and retried — generate again if this persists.";
  }
  if (/unexpected ['"]null['"]/i.test(text)) {
    return "Snowflake rejected a null argument. Pick a git repository or paste a real origin URL.";
  }
  return text;
}

const sameName = (a?: string | null, b?: string | null) => (a || "").toUpperCase() === (b || "").toUpperCase();

function GrantHint({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900">
      <p className="mb-1 flex items-center gap-1 font-medium"><Lock className="h-3.5 w-3.5" /> Your role can&apos;t use this repository&apos;s integration</p>
      <p className="mb-1.5">Ask an admin to run:</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-white px-2 py-1 font-mono">{sql}</code>
        <Button type="button" size="sm" variant="outline" className="h-7"
          onClick={() => { navigator.clipboard?.writeText(sql); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          <Copy className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

export function DbtStudio({
  runId, runName, domainName, canGenerate, generation, artifacts, branch,
  appliedSkills, lastWorkspace, workspace, publication, github,
}: {
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
}) {
  const slug = runName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "run";
  const allRepos = workspace?.git_repositories ?? [];
  const integrations = (workspace?.integrations ?? []).filter((i) => !i.provider || i.provider === "GIT_HTTPS_API");
  const projects = workspace?.dbt_projects ?? [];
  const role = workspace?.role || "";
  const publisherReady = Boolean(github?.ready || workspace?.capabilities?.github_publish);
  // Start from the saved integration when this role can use it, else the first usable one; then pick a
  // repository bound to that integration that this role can FETCH (never a repo from another integration).
  const savedInt = integrations.find((i) => sameName(i.name, String(branch?.api_integration ?? "")) && i.usable !== false);
  const firstUsableRepo = allRepos.find((r) => r.usable !== false);
  const startInt = savedInt?.name
    ?? firstUsableRepo?.api_integration
    ?? integrations.find((i) => i.usable !== false)?.name
    ?? integrations[0]?.name ?? "";
  const reposForStart = allRepos.filter((r) => sameName(r.api_integration, startInt));
  const startRepo = reposForStart.find((r) => sameName(r.fqn, String(branch?.git_repository ?? "")) && r.usable !== false)
    ?? reposForStart.find((r) => r.usable !== false) ?? reposForStart[0];
  const startPrefixes = integrations.find((i) => sameName(i.name, startInt))?.allowed_prefixes ?? [];
  const [integration, setIntegration] = useState(startInt);
  const [showAllRepos, setShowAllRepos] = useState(false);
  const repos = useMemo(
    () => (showAllRepos || !integration ? allRepos : allRepos.filter((r) => sameName(r.api_integration, integration))),
    [allRepos, integration, showAllRepos],
  );
  const [gitRepo, setGitRepo] = useState(startRepo?.fqn ?? "");
  const selectedRepo: DbtRepo | undefined = allRepos.find((r) => sameName(r.fqn, gitRepo));
  const selectedInt = integrations.find((i) => sameName(i.name, integration));
  const prefixes = selectedInt?.allowed_prefixes ?? [];
  const savedOrigin = String(branch?.origin ?? branch?.repo ?? "");
  const [origin, setOrigin] = useState(
    startRepo?.origin || (savedOrigin && originOk(savedOrigin, startPrefixes) ? savedOrigin : startPrefixes[0] || savedOrigin),
  );
  const [baseBranch, setBaseBranch] = useState(String(branch?.base_branch ?? "main"));
  const [cutBranch, setCutBranch] = useState(String(branch?.cut_branch ?? `feat/gdp-${slug}`));
  const [dbtProject, setDbtProject] = useState(String(branch?.dbt_project ?? ""));
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
  const [setupLog, setSetupLog] = useState<{ sql: string; ok: boolean; error?: string }[]>([]);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(artifacts.find((a) => a.file_path.endsWith(".sql"))?.file_path ?? artifacts[0]?.file_path ?? "");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"generate" | "push" | "publish" | "setup" | "repo" | "enhance" | "apply" | null>(null);
  const [pending, start] = useTransition();
  const models: CortexModel[] = workspace?.models ?? [];
  const [model, setModel] = useState(workspace?.default_model || models[0]?.name || "claude-sonnet-4-5");
  const [enhancePrompt, setEnhancePrompt] = useState("");
  const [preview, setPreview] = useState<{ content: string; rationale?: string; summary?: string; model?: string } | null>(null);

  /** Picking an integration cascades: its repositories → the first usable one → its origin → its branches. */
  const chooseIntegration = (name: string) => {
    setIntegration(name);
    setShowAllRepos(false);
    const forInt = allRepos.filter((r) => sameName(r.api_integration, name));
    const next = forInt.find((r) => r.usable !== false) ?? forInt[0];
    const intPrefixes = integrations.find((i) => sameName(i.name, name))?.allowed_prefixes ?? [];
    setGitRepo(next?.fqn ?? "");
    setOrigin(next?.origin || intPrefixes[0] || "");
    if (!next) {
      setBranches([]);
      setLatestBranch("");
      setGrantSql(null);
      setFetchNote(intPrefixes.length
        ? `No Snowflake git repository uses ${name} yet. Create one below to read branches.`
        : "");
    }
    const repoName = (intPrefixes[0] || "").split("/").filter(Boolean).pop() || "";
    setNewRepoName(repoName.replace(/[^A-Za-z0-9_]/g, "_").toUpperCase());
  };

  const refreshBranches = (repo: string, preferLatest = false) => {
    if (!repo.trim()) {
      setBranches([]);
      setLatestBranch("");
      return;
    }
    setListing(true);
    setGrantSql(null);
    start(async () => {
      const result = await listDbtBranches(runId, repo.trim(), true);
      setListing(false);
      if (!result.ok) {
        setFetchNote(result.error);
        return;
      }
      setBranches(result.data.branches);
      setLatestBranch(result.data.latest);
      setGrantSql(result.data.grant_sql ?? null);
      setFetchNote(result.data.fetched
        ? `Fetched ${result.data.branches.length} branch${result.data.branches.length === 1 ? "" : "es"} from ${result.data.repo}.`
        : result.data.grant_sql
          ? `Could not FETCH; showing branches from the last fetch (${result.data.branches.length}).`
          : (result.data.fetch_warning || "Listed the local clone without a fresh FETCH."));
      if (preferLatest || !baseBranch.trim() || baseBranch === "main" || !result.data.branches.some((b) => b.name === baseBranch)) {
        if (result.data.latest) setBaseBranch(result.data.latest);
      }
    });
  };

  useEffect(() => {
    if (gitRepo.trim()) refreshBranches(gitRepo, !branch?.base_branch);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload whenever the selected repository changes
  }, [gitRepo]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? artifacts.filter((a) => `${a.file_path} ${a.artifact_type}`.toLowerCase().includes(q)) : artifacts;
  }, [artifacts, query]);
  const current = visible.find((a) => a.file_path === open) ?? visible[0] ?? artifacts[0];
  const originValid = originOk(origin, prefixes);
  const isGithub = /^(https:\/\/github\.com\/|git@github\.com:)[^/]+\/[^/]+/i.test(origin.trim());
  const repoReady = Boolean(gitRepo.trim());
  const sameBranch = baseBranch.trim() !== "" && baseBranch.trim() === cutBranch.trim();
  const ready = Boolean(baseBranch.trim() && cutBranch.trim() && originValid && !sameBranch
    && (!fetchSkeleton || repoReady) && (!push || (isGithub && publisherReady)));
  const skills = appliedSkills?.length
    ? appliedSkills
    : (workspace?.skills ?? []).map((s) => ({
      name: s.skill_name || s.name || "",
      version: s.version ?? undefined,
      description: s.description,
    }));
  const projectStatus = lastWorkspace && typeof lastWorkspace.dbt_project === "object"
    ? (lastWorkspace.dbt_project as { status?: string; detail?: string; dbt_project?: string })
    : null;
  const lineage = lastWorkspace && typeof lastWorkspace.lineage === "object"
    ? lastWorkspace.lineage as Record<string, unknown>
    : null;
  const skeleton = lineage && typeof lineage.skeleton === "object"
    ? lineage.skeleton as { requested?: boolean; files?: number; branch?: string; error?: string }
    : null;
  const pub = publication;
  const published = pub && ["PUBLISHED", "NO_CHANGES"].includes(pub.status);

  const steps: { title: string; detail: string; tone: StepTone }[] = [
    {
      title: "1. Read approved STTM",
      detail: "Deterministic models from the contract + DBT-ONBOARD-SOURCE / domain skill macros.",
      tone: artifacts.length ? "done" : "idle",
    },
    {
      title: `2. Read skeleton from ${skeleton?.branch || baseBranch}`,
      detail: !skeleton
        ? (fetchSkeleton ? `Runs when you generate: reads every file on ${baseBranch || "the cut-from branch"}.` : "Off — models only.")
        : skeleton.error
          ? String(skeleton.error)
          : skeleton.files
            ? `${skeleton.files} files from the cut-from branch, kept as-is`
            : skeleton.requested ? "The branch had no readable dbt files." : "Skipped. Generate used only the STTM models.",
      tone: !skeleton ? "idle" : skeleton.error ? "fail" : skeleton.files ? "done" : "skip",
    },
    {
      title: "3. Write compile-only project to stage",
      detail: generation
        ? `${generation.files_generated} files on ${generation.stage_path}`
        : "Not generated yet.",
      tone: generation ? "done" : "idle",
    },
    {
      title: `4. Push branch ${pub?.head_branch || cutBranch} to GitHub`,
      detail: pub
        ? pub.status === "FAILED"
          ? pub.detail || "Publish failed."
          : `${pub.files_pushed ?? "?"} files${pub.commit_sha ? ` · commit ${pub.commit_sha.slice(0, 7)}` : ""} on top of ${pub.base_branch}${pub.status === "NO_CHANGES" ? " · no changes vs base" : ""}`
        : publisherReady ? "Runs after generate when GitHub publishing is selected." : "GitHub publishing is not set up yet (see below).",
      tone: !pub ? (publisherReady ? "idle" : "skip") : pub.status === "FAILED" ? "fail" : "done",
    },
    {
      title: "5. Pull request",
      detail: pub?.pr_url ? `#${pub.pr_number} ${pub.pr_url}` : pub?.status === "NO_CHANGES" ? "No changes, so no PR was opened." : "Opened against the cut-from branch.",
      tone: pub?.pr_url ? "done" : pub?.status === "FAILED" ? "fail" : "idle",
    },
    {
      title: "6. dbt project in Snowflake",
      detail: pub?.dbt_project
        ? `${pub.dbt_project} · from @repo/branches/${pub.head_branch} (WRITEBACK=FALSE)`
        : projectStatus
          ? `${projectStatus.status || "unknown"}${projectStatus.dbt_project ? ` · ${projectStatus.dbt_project} (from stage)` : ""}${projectStatus.detail ? ` — ${projectStatus.detail}` : ""}`
          : "Created from the stage on generate, then from the pushed branch after publish.",
      tone: pub?.dbt_project || projectStatus?.status === "CREATED" ? "done" : projectStatus ? "fail" : "idle",
    },
  ];

  const applyRepo = (fqn: string) => {
    setGitRepo(fqn);
    const next = allRepos.find((r) => r.fqn === fqn);
    if (next?.origin) setOrigin(next.origin);
    if (next?.api_integration && !sameName(next.api_integration, integration)) setIntegration(next.api_integration);
  };

  const branchExists = branches.some((b) => b.name === cutBranch.trim());

  const runGenerate = (doPush: boolean) => {
    if (!baseBranch.trim() || !cutBranch.trim()) {
      setError("Cut-from and new branch are required.");
      return;
    }
    if (sameBranch) {
      setError("The new branch must differ from the cut-from branch.");
      return;
    }
    if (origin.trim() && prefixes.length && !originValid) {
      setError("Origin is not in the API integration allowed prefixes.");
      return;
    }
    if (fetchSkeleton && !gitRepo.trim()) {
      setError("Pick or create a Snowflake GIT REPOSITORY to read the skeleton, or turn the skeleton off.");
      return;
    }
    if (doPush && !isGithub) {
      setError("Pushing a branch and opening a PR needs a GitHub origin URL.");
      return;
    }
    start(async () => {
      setError("");
      setNotice("");
      setMode(doPush ? "push" : "generate");
      const result = await generateDbt(runId, {
        repo: origin.trim() || gitRepo || undefined,
        origin: origin.trim() || undefined,
        git_repository: gitRepo.trim() || undefined,
        api_integration: integration || undefined,
        dbt_project: dbtProject.trim() || undefined,
        allowed_prefixes: prefixes.length ? prefixes : undefined,
        base_branch: baseBranch.trim(),
        cut_branch: cutBranch.trim(),
        push: doPush,
        fetch_skeleton: fetchSkeleton,
      });
      setMode(null);
      if (!result.ok) setError(friendlyError(result.error));
      else setNotice(doPush ? "Generated. Check the push and pull request steps above." : "Generated to the stage.");
    });
  };

  const runPublish = () => start(async () => {
    setError("");
    setNotice("");
    setMode("publish");
    const result = await publishDbt(runId, {
      origin: origin.trim(), base_branch: baseBranch.trim(), cut_branch: cutBranch.trim(),
      git_repository: gitRepo.trim() || undefined, draft: draftPr,
    });
    setMode(null);
    if (!result.ok) { setError(friendlyError(result.error)); return; }
    if (result.data.status === "FAILED" || result.data.status === "NOT_CONFIGURED") setError(result.data.detail || result.data.status);
    else setNotice(result.data.pull_request?.url ? `Pull request ready: ${result.data.pull_request.url}` : "Branch pushed; nothing changed vs the base branch.");
  });

  const runSetup = () => start(async () => {
    setError("");
    setMode("setup");
    const result = await setupGithubPublishing(runId, { token: token.trim() || undefined });
    setMode(null);
    setToken("");
    if (!result.ok) { setError(result.error); return; }
    setSetupLog(result.data.log);
    if (!result.data.ready) setError(result.data.detail || "Setup did not finish.");
    else setNotice("GitHub publishing is ready.");
  });

  const runCreateRepo = () => start(async () => {
    setError("");
    setMode("repo");
    const result = await createGitRepository(runId, {
      name: newRepoName || "DBT_REPO", origin: origin.trim() || prefixes[0] || "", api_integration: integration,
    });
    setMode(null);
    if (!result.ok) { setError(friendlyError(result.error)); return; }
    setGitRepo(result.data.git_repository);
    setNotice(`Created ${result.data.git_repository} and fetched it.`);
  });

  const runEnhance = () => {
    if (!current) {
      setError("Open a generated file first.");
      return;
    }
    if (!enhancePrompt.trim()) {
      setError("Describe the change you want Cortex to make.");
      return;
    }
    start(async () => {
      setError("");
      setMode("enhance");
      const result = await previewDbtEnhance(runId, {
        file_path: current.file_path,
        prompt: enhancePrompt.trim(),
        model: model || undefined,
      });
      setMode(null);
      if (!result.ok) {
        setError(friendlyError(result.error));
        return;
      }
      setPreview({
        content: result.data.content,
        rationale: result.data.rationale,
        summary: result.data.summary,
        model: result.data.model,
      });
    });
  };

  const runApply = () => {
    if (!current || !preview) return;
    start(async () => {
      setError("");
      setMode("apply");
      const result = await applyDbtEnhance(runId, { file_path: current.file_path, content: preview.content });
      setMode(null);
      if (!result.ok) {
        setError(friendlyError(result.error));
        return;
      }
      setPreview(null);
      setEnhancePrompt("");
    });
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>1. Snowflake git connection</CardTitle>
          <CardDescription>
            Pick an API integration. Its Snowflake git repositories, allowed origin URLs and branches load automatically.
            The repository clone is used read-only, to read the cut-from branch skeleton.
            {domainName ? ` Domain: ${domainName}.` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!!workspace?.warnings?.length && (
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">{workspace.warnings.length} discovery note(s)</summary>
              <ul className="mt-1 list-inside list-disc">{workspace.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </details>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="git_int">API integration (git)</Label>
              <Select id="git_int" value={integration} onChange={(e) => chooseIntegration(e.target.value)}>
                <option value="">Select integration</option>
                {integrations.map((i) => {
                  const count = allRepos.filter((r) => sameName(r.api_integration, i.name)).length;
                  return (
                    <option key={i.name} value={i.name}>
                      {i.name}{i.usable === false ? " · no USAGE" : ""} · {count} repo{count === 1 ? "" : "s"}
                    </option>
                  );
                })}
              </Select>
              {selectedInt?.usable === false && (
                <p className="mt-1 text-xs text-amber-700">
                  This role can&apos;t describe {selectedInt.name}. Run: <code className="font-mono">GRANT USAGE ON INTEGRATION &quot;{selectedInt.name}&quot; TO ROLE {role || "<role>"};</code>
                </p>
              )}
            </div>
            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="git_repo">Snowflake GIT REPOSITORY</Label>
                {integration && allRepos.length > repos.length && (
                  <button type="button" className="mt-3 text-[11px] text-muted-foreground underline" onClick={() => setShowAllRepos(true)}>
                    show all {allRepos.length}
                  </button>
                )}
              </div>
              {repos.length > 0 ? (
                <Select id="git_repo" value={gitRepo} onChange={(e) => applyRepo(e.target.value)}>
                  <option value="">Select repository</option>
                  {repos.map((r) => (
                    <option key={r.fqn} value={r.fqn}>{r.fqn}{r.usable === false ? " · no access" : ""}</option>
                  ))}
                </Select>
              ) : (
                <div className="space-y-2 rounded-md border border-dashed p-2.5">
                  <p className="text-xs text-muted-foreground">
                    {integration ? `No repository clone uses ${integration} yet.` : "Pick an integration first."}
                    {integration && " Create one to read its branches (one-time, read-only)."}
                  </p>
                  {integration && (
                    <div className="flex gap-2">
                      <Input value={newRepoName} onChange={(e) => setNewRepoName(e.target.value.toUpperCase())} placeholder="DBT_DEMO" className="h-8 font-mono text-xs" />
                      <Button type="button" size="sm" disabled={pending || !(origin.trim() || prefixes[0])} onClick={runCreateRepo}>
                        {pending && mode === "repo" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Create clone
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          {(selectedRepo?.grant_sql || grantSql) && <GrantHint sql={(grantSql || selectedRepo?.grant_sql) as string} />}
          <div>
            <Label htmlFor="origin">Origin URL</Label>
            <Input
              id="origin"
              value={origin}
              onChange={(e) => setOrigin(e.target.value)}
              placeholder={prefixes[0] || "https://github.com/org/repo"}
              aria-invalid={!originValid}
            />
            {!originValid && (
              <p className="mt-1 text-xs text-destructive">
                Origin must start with an allowed prefix for {integration || "the selected integration"}.
              </p>
            )}
          </div>
          {prefixes.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">Allowed URL prefixes</p>
              <div className="flex flex-wrap gap-1">
                {prefixes.map((p) => (
                  <button key={p} type="button" onClick={() => setOrigin(p)}
                    className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${origin.trim().startsWith(p) ? "border-primary bg-primary/5" : "hover:bg-muted"}`}>
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}
          {projects.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Existing dbt projects: {projects.map((p) => p.fqn).join(", ")}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>2. Skills from the STTM</CardTitle>
          <CardDescription>
            Models are built from the approved STTM with DBT-ONBOARD-SOURCE and added on top of the cut-from branch.
            Files that aren&apos;t generated stay exactly as they are on that branch.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {skills.length === 0 && <Badge variant="outline">DBT-ONBOARD-SOURCE</Badge>}
          {skills.map((s) => (
            <Badge key={s.name} variant="secondary" title={s.description}>
              {s.name}{s.version ? ` v${s.version}` : ""}
            </Badge>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How this run produces code</CardTitle>
          <CardDescription>
            Snowflake git repository clones are read-only from SQL. The skeleton is read from the clone, and the new branch,
            commit and pull request go through the GitHub API (CODEGEN.PUBLISH_DBT_PR, using a Snowflake secret). Then the
            pushed branch is fetched back into Snowflake as a compile-only dbt project.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 md:grid-cols-2">
          {steps.map((step) => (
            <div key={step.title} className={`rounded-lg border px-3 py-2 ${stepTone(step.tone)}`}>
              <p className="text-sm font-medium">{step.title}</p>
              <p className="mt-1 break-all text-xs text-muted-foreground">{step.detail}</p>
            </div>
          ))}
          {pub?.pr_url && (
            <a href={pub.pr_url} target="_blank" rel="noreferrer"
              className="flex items-center gap-2 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800 md:col-span-2">
              <GitPullRequest className="h-4 w-4" /> Open pull request #{pub.pr_number} on GitHub <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
        </CardContent>
      </Card>

      {!publisherReady && (
        <Card className="border-amber-300">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><GitPullRequest className="h-4 w-4" /> Set up GitHub publishing (one time)</CardTitle>
            <CardDescription>
              Creates a network rule for api.github.com, a Snowflake SECRET holding a GitHub token, an external access
              integration, and the CODEGEN.PUBLISH_DBT_PR procedure. The token goes straight into the Snowflake secret;
              it isn&apos;t stored by this app. Use a fine-grained token with <b>Contents: read/write</b> and
              <b> Pull requests: read/write</b> on the repository. This needs a role that can create integrations and secrets.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
                placeholder="github_pat_… (leave empty if CODEGEN.GITHUB_TOKEN already exists)" className="max-w-md" />
              <Button type="button" disabled={pending} onClick={runSetup}>
                {pending && mode === "setup" && <Loader2 className="h-4 w-4 animate-spin" />} Set up publishing
              </Button>
            </div>
            {setupLog.length > 0 && (
              <ol className="space-y-1 text-xs">
                {setupLog.map((s) => (
                  <li key={s.sql} className={`rounded border px-2 py-1 font-mono ${s.ok ? "border-emerald-200 bg-emerald-50" : "border-destructive/30 bg-destructive/5"}`}>
                    <span className="block whitespace-pre-wrap break-all">{s.sql}</span>
                    {s.error && <span className="mt-0.5 block font-sans text-destructive">{s.error}</span>}
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>3. Cut branch, generate and open PR</CardTitle>
          <CardDescription>
            Read the cut-from branch, write the STTM models to the stage and a compile-only dbt project, then push a new
            branch to GitHub and open a pull request.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <Label htmlFor="base">Cut from (existing branch)</Label>
              {branches.length > 0 ? (
                <Select id="base" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)}>
                  {branches.map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name}{b.name === latestBranch ? " · latest" : ""}{b.last_modified ? ` · ${b.last_modified.slice(0, 16)}` : ""}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input id="base" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} placeholder="main" />
              )}
            </div>
            <div>
              <Label htmlFor="cut">New branch to create</Label>
              <Input id="cut" value={cutBranch} onChange={(e) => setCutBranch(e.target.value)} placeholder={`feat/gdp-${slug}`} />
              {branchExists && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {cutBranch} already exists. Publishing adds a new commit on it and reuses its open PR.
                </p>
              )}
              {sameBranch && <p className="mt-1 text-xs text-destructive">Must differ from the cut-from branch.</p>}
            </div>
            <div>
              <Label htmlFor="dbt_proj">dbt project (optional)</Label>
              <Input
                id="dbt_proj"
                value={dbtProject}
                onChange={(e) => setDbtProject(e.target.value)}
                placeholder={projects[0]?.fqn || "DEV_AI_PLATFORM.CODEGEN.PIPELINE_RUN"}
                list="dbt_proj_list"
              />
              {projects.length > 0 && (
                <datalist id="dbt_proj_list">
                  {projects.map((p) => <option key={p.fqn} value={p.fqn} />)}
                </datalist>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" disabled={!gitRepo.trim() || listing} onClick={() => refreshBranches(gitRepo, true)}>
              {listing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {listing ? "Fetching branches…" : "Refresh branches"}
            </Button>
            {latestBranch && latestBranch !== baseBranch && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setBaseBranch(latestBranch)}>
                <GitBranchIcon className="h-4 w-4" /> Use latest ({latestBranch})
              </Button>
            )}
            {fetchNote && <span className="text-xs text-muted-foreground">{fetchNote}</span>}
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium">Destination</p>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="dest" checked={push} onChange={() => setPush(true)} disabled={!publisherReady} />
              <span>
                GitHub branch + pull request: cut <span className="font-mono">{cutBranch || "feat/gdp-…"}</span> from{" "}
                <span className="font-mono">{baseBranch || "latest"}</span>, commit the models and open a PR.
                {!publisherReady && <span className="block text-xs text-amber-700">Set up GitHub publishing above first.</span>}
                {publisherReady && !isGithub && origin.trim() && <span className="block text-xs text-amber-700">The origin must be a github.com repository.</span>}
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="dest" checked={!push} onChange={() => setPush(false)} />
              <span>Stage only: write <span className="font-mono">@CODEGEN.DBT_STAGE</span> and the dbt project, without touching git.</span>
            </label>
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={fetchSkeleton} onChange={(e) => setFetchSkeleton(e.target.checked)} />
              Build on the cut-from branch skeleton
            </label>
            {push && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={draftPr} onChange={(e) => setDraftPr(e.target.checked)} /> Open as draft PR
              </label>
            )}
          </div>
          {generation && (
            <p className="text-sm">
              Version {generation.generation_version}{" "}
              <Badge variant="outline">{generation.generation_status}</Badge>
              {" · "}{generation.files_generated} files · {generation.stage_path}
              {pub && <> · last publish <Badge variant={published ? "success" : "destructive"}>{pub.status}</Badge></>}
            </p>
          )}
          {notice && <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}
          {error && <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
          {canGenerate && (
            <div className="flex flex-wrap gap-2">
              <Button disabled={pending || !ready} onClick={() => runGenerate(push)}>
                {pending && mode === (push ? "push" : "generate") && <Loader2 className="h-4 w-4 animate-spin" />}
                {pending && mode === "push" ? "Generating, pushing and opening PR…"
                  : pending && mode === "generate" ? "Generating…"
                    : push ? "Generate, push branch & open PR" : "Generate to stage"}
              </Button>
              {generation && publisherReady && (
                <Button variant="outline" disabled={pending || !isGithub || sameBranch} onClick={runPublish}>
                  {pending && mode === "publish" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />}
                  {pub && published ? "Push again & update PR" : `Push v${generation.generation_version} & open PR`}
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
      {artifacts.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Generated code</CardTitle>
            <CardDescription>
              {visible.length} files{cutBranch ? ` targeting ${cutBranch}` : ""}. Search and open any SQL or YAML.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input aria-label="Search files" placeholder="Search files…" value={query} onChange={(e) => setQuery(e.target.value)} />
            {visible.length === 0 && <p className="text-sm text-muted-foreground">No files match.</p>}
            {current && (
              <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
                <ol className="max-h-[28rem] space-y-1 overflow-auto">
                  {visible.map((a) => (
                    <li key={a.artifact_id}>
                      <button
                        type="button"
                        onClick={() => setOpen(a.file_path)}
                        className={`w-full truncate rounded-md px-2 py-1.5 text-left font-mono text-xs ${a.file_path === current.file_path ? "bg-accent" : "hover:bg-muted"}`}
                      >
                        {a.file_path}
                      </button>
                    </li>
                  ))}
                </ol>
                <div>
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm">{current.file_path}</span>
                    <Badge variant="outline">{current.artifact_type}</Badge>
                  </div>
                  <pre className="max-h-[28rem] overflow-auto rounded-md bg-muted p-3 text-xs">{preview?.content ?? current.content}</pre>
                  <div className="mt-4 space-y-3 rounded-lg border p-3">
                    <p className="text-sm font-medium">Enhance this file with Cortex</p>
                    <p className="text-xs text-muted-foreground">
                      Pick a model available to this Snowflake account. Preview the rewrite, then apply it
                      onto the generated artifact. This does not push git or open a PR.
                    </p>
                    <div className="grid gap-3 md:grid-cols-[220px_1fr]">
                      <div>
                        <Label htmlFor="cortex_model">Model</Label>
                        <Select id="cortex_model" value={model} onChange={(e) => setModel(e.target.value)}>
                          {(models.length ? models : [{ name: model, family: "other" } as CortexModel]).map((m) => (
                            <option key={m.name} value={m.name}>
                              {m.family ? `${m.family} · ` : ""}{m.name}
                            </option>
                          ))}
                        </Select>
                      </div>
                      <div>
                        <Label htmlFor="enhance_need">What should change</Label>
                        <Textarea
                          id="enhance_need"
                          rows={2}
                          value={enhancePrompt}
                          onChange={(e) => setEnhancePrompt(e.target.value)}
                          placeholder="Example: use the clean_boolean macro, add freshness tests, document grain."
                        />
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {["Harden nulls and tests", "Use existing macros", "Document columns", "Simplify Jinja"].map((hint) => (
                        <Button key={hint} type="button" size="sm" variant="ghost" onClick={() => setEnhancePrompt(hint)}>
                          {hint}
                        </Button>
                      ))}
                    </div>
                    {preview?.rationale && (
                      <p className="text-sm text-muted-foreground">
                        {preview.model ? `${preview.model}: ` : ""}{preview.summary || preview.rationale}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" variant="outline" disabled={pending || !enhancePrompt.trim()} onClick={runEnhance}>
                        {pending && mode === "enhance" && <Loader2 className="h-4 w-4 animate-spin" />}
                        {pending && mode === "enhance" ? "Asking Cortex…" : "Preview enhance"}
                      </Button>
                      <Button type="button" disabled={pending || !preview} onClick={runApply}>
                        {pending && mode === "apply" && <Loader2 className="h-4 w-4 animate-spin" />}
                        {pending && mode === "apply" ? "Applying…" : "Apply to this file"}
                      </Button>
                      {preview && (
                        <Button type="button" variant="ghost" disabled={pending} onClick={() => setPreview(null)}>
                          Discard preview
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
