"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { applyDbtEnhance, generateDbt, listDbtBranches, previewDbtEnhance } from "../pipeline-actions";
import type { DbtArtifact, CortexModel, DbtGeneration, DbtWorkspace, GitBranch } from "./dbt-types";

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

export function DbtStudio({
  runId, runName, domainName, canGenerate, generation, artifacts, branch,
  appliedSkills, lastWorkspace, workspace,
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
}) {
  const slug = runName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "run";
  const repos = workspace?.git_repositories ?? [];
  const integrations = workspace?.integrations ?? [];
  const projects = workspace?.dbt_projects ?? [];
  const firstRepo = repos[0];
  const [gitRepo, setGitRepo] = useState(String(branch?.git_repository ?? firstRepo?.fqn ?? ""));
  const selectedRepo = repos.find((r) => r.fqn === gitRepo);
  const [integration, setIntegration] = useState(String(
    branch?.api_integration ?? selectedRepo?.api_integration ?? firstRepo?.api_integration ?? integrations[0]?.name ?? "",
  ));
  const selectedInt = integrations.find((i) => i.name === integration);
  const prefixes = selectedInt?.allowed_prefixes ?? [];
  const [origin, setOrigin] = useState(String(branch?.origin ?? branch?.repo ?? selectedRepo?.origin ?? firstRepo?.origin ?? ""));
  const [baseBranch, setBaseBranch] = useState(String(branch?.base_branch ?? "main"));
  const [cutBranch, setCutBranch] = useState(String(branch?.cut_branch ?? `feat/gdp-${slug}`));
  const [dbtProject, setDbtProject] = useState(String(branch?.dbt_project ?? ""));
  const [push, setPush] = useState(Boolean(branch?.push ?? true));
  const [fetchSkeleton, setFetchSkeleton] = useState(branch?.fetch_skeleton !== false);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [latestBranch, setLatestBranch] = useState("");
  const [fetchNote, setFetchNote] = useState("");
  const [listing, setListing] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(artifacts.find((a) => a.file_path.endsWith(".sql"))?.file_path ?? artifacts[0]?.file_path ?? "");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"generate" | "push" | "enhance" | "apply" | null>(null);
  const [pending, start] = useTransition();
  const models = workspace?.models ?? [];
  const [model, setModel] = useState(workspace?.default_model || models[0]?.name || "claude-sonnet-4-5");
  const [enhancePrompt, setEnhancePrompt] = useState("");
  const [preview, setPreview] = useState<{ content: string; rationale?: string; summary?: string; model?: string } | null>(null);

  useEffect(() => {
    if (origin.trim()) return;
    if (selectedRepo?.origin) {
      setOrigin(selectedRepo.origin);
      return;
    }
    if (prefixes[0]) setOrigin(prefixes[0]);
  }, [origin, prefixes, selectedRepo]);

  const refreshBranches = (repo: string, preferLatest = false) => {
    if (!repo.trim()) {
      setBranches([]);
      setLatestBranch("");
      return;
    }
    setListing(true);
    start(async () => {
      const result = await listDbtBranches(runId, repo.trim(), true);
      setListing(false);
      if (!result.ok) {
        setFetchNote(result.error);
        return;
      }
      setBranches(result.data.branches);
      setLatestBranch(result.data.latest);
      setFetchNote(result.data.fetched
        ? `Fetched ${result.data.branches.length} branch${result.data.branches.length === 1 ? "" : "es"} from ${result.data.repo}.`
        : (result.data.fetch_warning || "Listed local git stage without a fresh FETCH."));
      if (preferLatest || !baseBranch.trim() || baseBranch === "main" || !result.data.branches.some((b) => b.name === baseBranch)) {
        if (result.data.latest) setBaseBranch(result.data.latest);
      }
    });
  };

  useEffect(() => {
    if (gitRepo.trim()) refreshBranches(gitRepo, !branch?.base_branch);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per selected repo
  }, [gitRepo]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? artifacts.filter((a) => `${a.file_path} ${a.artifact_type}`.toLowerCase().includes(q)) : artifacts;
  }, [artifacts, query]);
  const current = visible.find((a) => a.file_path === open) ?? visible[0] ?? artifacts[0];
  const originValid = originOk(origin, prefixes);
  const needsRepo = fetchSkeleton || push;
  const repoReady = Boolean(gitRepo.trim());
  const ready = Boolean(baseBranch.trim() && cutBranch.trim() && originValid && (!needsRepo || repoReady));
  const skills = appliedSkills?.length
    ? appliedSkills
    : (workspace?.skills ?? []).map((s) => ({
      name: s.skill_name || s.name || "",
      version: s.version ?? undefined,
      description: s.description,
    }));
  const pushStatus = lastWorkspace && typeof lastWorkspace.push === "object"
    ? (lastWorkspace.push as { status?: string; detail?: string; method?: string; pull_request?: boolean; repo?: string; branch?: string })
    : null;
  const projectStatus = lastWorkspace && typeof lastWorkspace.dbt_project === "object"
    ? (lastWorkspace.dbt_project as { status?: string; detail?: string; dbt_project?: string })
    : null;
  const lineage = lastWorkspace && typeof lastWorkspace.lineage === "object"
    ? lastWorkspace.lineage as Record<string, unknown>
    : null;
  const skeleton = lineage && typeof lineage.skeleton === "object"
    ? lineage.skeleton as { requested?: boolean; files?: number; branch?: string; error?: string }
    : { requested: fetchSkeleton, files: Number(branch?.skeleton_files ?? 0), branch: baseBranch, error: String(branch?.skeleton_error ?? "") };
  const pr = lastWorkspace && typeof lastWorkspace.pull_request === "object"
    ? lastWorkspace.pull_request as { created?: boolean; reason?: string }
    : { created: false, reason: "The Agentic pipeline copies files onto the Snowflake git branch. It does not open a GitHub or GitLab pull request." };

  const steps: { title: string; detail: string; tone: StepTone }[] = [
    {
      title: "1. Read approved STTM",
      detail: "Deterministic models from the contract + DBT-ONBOARD-SOURCE / domain skill macros.",
      tone: artifacts.length ? "done" : "idle",
    },
    {
      title: `2. Fetch skeleton from ${skeleton.branch || baseBranch}`,
      detail: skeleton.error
        ? String(skeleton.error)
        : skeleton.files
          ? `${skeleton.files} files from the cut-from branch`
          : skeleton.requested
            ? "Requested. No files came back — check the git repository and branch name."
            : "Skipped. Generate used only the STTM skeleton.",
      tone: skeleton.error ? "fail" : skeleton.files ? "done" : skeleton.requested ? "fail" : "skip",
    },
    {
      title: "3. Write compile-only project to stage",
      detail: generation
        ? `${generation.files_generated} files on ${generation.stage_path}`
        : "Not generated yet.",
      tone: generation ? "done" : "idle",
    },
    {
      title: "4. CREATE DBT PROJECT (WRITEBACK=FALSE)",
      detail: projectStatus
        ? `${projectStatus.status || "unknown"}${projectStatus.dbt_project ? ` · ${projectStatus.dbt_project}` : ""}${projectStatus.detail ? ` — ${projectStatus.detail}` : ""}`
        : "Runs only when you generate.",
      tone: projectStatus?.status === "CREATED" ? "done" : projectStatus ? "fail" : "idle",
    },
    {
      title: `5. COPY FILES onto git branch ${cutBranch}`,
      detail: pushStatus
        ? `${pushStatus.status}${pushStatus.method ? ` via ${pushStatus.method}` : ""}${pushStatus.detail ? ` — ${pushStatus.detail}` : ""}`
        : "Not requested, or generate-to-stage only.",
      tone: pushStatus?.status === "PUSHED" ? "done" : pushStatus?.status === "STAGE_ONLY" || pushStatus?.status === "FETCH_FAILED" ? "fail" : "skip",
    },
    {
      title: "6. Pull request",
      detail: pr.reason || "Not created.",
      tone: "skip",
    },
  ];

  const applyRepo = (fqn: string) => {
    setGitRepo(fqn);
    const next = repos.find((r) => r.fqn === fqn);
    if (next?.origin) setOrigin(next.origin);
    if (next?.api_integration) setIntegration(next.api_integration);
  };

  const branchExists = branches.some((b) => b.name === cutBranch.trim());

  const runGenerate = (doPush: boolean) => {
    if (!baseBranch.trim() || !cutBranch.trim()) {
      setError("Cut-from and new branch are required.");
      return;
    }
    if (origin.trim() && prefixes.length && !originValid) {
      setError("Origin is not in the API integration allowed prefixes.");
      return;
    }
    if ((doPush || fetchSkeleton) && !gitRepo.trim()) {
      setError("Enter the Snowflake GIT REPOSITORY name to fetch a skeleton or push a branch.");
      return;
    }
    start(async () => {
      setError("");
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
    });
  };

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
            Use the same API integration and GIT REPOSITORY Snowflake Workspace uses.
            Allowed URL prefixes come from the integration. Generate still works without a
            listed repo if you type the fully qualified name.
            {domainName ? ` Domain: ${domainName}.` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!!workspace?.warnings?.length && (
            <p className="text-xs text-muted-foreground">
              {workspace.warnings.filter((w) => !w.startsWith("SHOW GIT REPOSITORIES IN")).slice(0, 1)[0]
                || "Could not list every Snowflake git object. Type names below."}
            </p>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="git_int">API integration</Label>
              <Select id="git_int" value={integration} onChange={(e) => setIntegration(e.target.value)}>
                <option value="">Select integration</option>
                {integrations.map((i) => (
                  <option key={i.name} value={i.name}>{i.name}{i.type ? ` · ${i.type}` : ""}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="git_repo">Snowflake GIT REPOSITORY</Label>
              {repos.length > 0 ? (
                <Select id="git_repo" value={gitRepo} onChange={(e) => applyRepo(e.target.value)}>
                  <option value="">Select repository</option>
                  {repos.map((r) => (
                    <option key={r.fqn} value={r.fqn}>{r.fqn}</option>
                  ))}
                </Select>
              ) : (
                <Input
                  id="git_repo"
                  value={gitRepo}
                  onChange={(e) => setGitRepo(e.target.value)}
                  placeholder="DEV_AI_PLATFORM.CODEGEN.DBT_DEMO"
                />
              )}
              {repos.length === 0 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  No repository listed in this account. Paste the fully qualified name, or generate without push.
                </p>
              )}
            </div>
          </div>
          <div>
            <Label htmlFor="origin">Origin URL</Label>
            <Input
              id="origin"
              value={origin}
              onChange={(e) => setOrigin(e.target.value)}
              placeholder={prefixes[0] || "https://github.com/org/repo.git"}
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
                {prefixes.map((p) => <Badge key={p} variant="outline">{p}</Badge>)}
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
            Models are built from the approved STTM with DBT-ONBOARD-SOURCE, then overlaid on the
            cut-from branch skeleton when a repository is set.
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
            Generation is deterministic from the STTM. Git is optional: we FETCH the cut-from
            branch as skeleton, then COPY FILES onto the new branch path in the Snowflake
            GIT REPOSITORY. That can create the branch path. It never opens a pull request.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 md:grid-cols-2">
          {steps.map((step) => (
            <div key={step.title} className={`rounded-lg border px-3 py-2 ${stepTone(step.tone)}`}>
              <p className="text-sm font-medium">{step.title}</p>
              <p className="mt-1 text-xs text-muted-foreground">{step.detail}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>3. Cut branch and generate</CardTitle>
          <CardDescription>
            Fetch the base branch, write STTM models to the stage, CREATE DBT PROJECT with
            WRITEBACK=FALSE, then optionally COPY FILES onto the new git branch.
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
                      {b.name}{b.name === latestBranch ? " · latest" : ""}{b.last_modified ? ` · ${b.last_modified}` : ""}
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
                  {cutBranch} already exists. Push will overwrite files on that branch.
                </p>
              )}
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
              {(listing || pending) && <Loader2 className="h-4 w-4 animate-spin" />}
              {listing ? "Fetching branches…" : "FETCH repo and list branches"}
            </Button>
            {latestBranch && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setBaseBranch(latestBranch)}>
                Use latest ({latestBranch})
              </Button>
            )}
          </div>
          {fetchNote && <p className="text-xs text-muted-foreground">{fetchNote}</p>}
          {branches.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {branches.length} remote branch{branches.length === 1 ? "" : "es"} after FETCH.
              Cut from the latest unless you pick another.
            </p>
          )}
          <div className="space-y-2">
            <p className="text-sm font-medium">Version control destination</p>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="dest" checked={!push} onChange={() => setPush(false)} />
              <span>
                Stage only — write <span className="font-mono">@CODEGEN.DBT_STAGE</span> and the dbt project.
                No git branch is created.
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="dest" checked={push} onChange={() => { setPush(true); setFetchSkeleton(true); }} />
              <span>
                Snowflake git branch — cut from <span className="font-mono">{baseBranch || "latest"}</span>,
                create <span className="font-mono">{cutBranch || "feat/gdp-…"}</span> with COPY FILES.
                That is version control in the GIT REPOSITORY. Still no GitHub PR.
              </span>
            </label>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={fetchSkeleton} onChange={(e) => setFetchSkeleton(e.target.checked)} />
            Overlay STTM models onto the skeleton from the cut-from branch
          </label>
          {needsRepo && !repoReady && (
            <p className="text-xs text-muted-foreground">
              Skeleton fetch and push need a Snowflake GIT REPOSITORY name. Leave both unchecked to generate onto the stage only.
            </p>
          )}
          {typeof branch?.instruction === "string" && (
            <p className="text-sm text-muted-foreground">{branch.instruction}</p>
          )}
          {generation && (
            <p className="text-sm">
              Version {generation.generation_version}{" "}
              <Badge variant="outline">{generation.generation_status}</Badge>
              {" · "}{generation.files_generated} files · {generation.stage_path}
            </p>
          )}
          {pushStatus?.status && (
            <p className="text-sm">
              Last push: <Badge variant={pushStatus.status === "PUSHED" ? "success" : "outline"}>{pushStatus.status}</Badge>
              {pushStatus.detail ? ` · ${pushStatus.detail}` : ""}
            </p>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {canGenerate && (
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={pending || !baseBranch.trim() || !cutBranch.trim() || !originValid}
                onClick={() => runGenerate(false)}
                variant="outline"
              >
                {pending && mode === "generate" && <Loader2 className="h-4 w-4 animate-spin" />}
                {pending && mode === "generate" ? "Generating…" : "Generate to stage only"}
              </Button>
              <Button
                disabled={pending || !ready}
                onClick={() => runGenerate(push)}
              >
                {pending && mode === "push" && <Loader2 className="h-4 w-4 animate-spin" />}
                {pending && mode === "push"
                  ? (push ? "Generating and pushing…" : "Generating…")
                  : push ? "Generate, create project, and push" : "Generate dbt project"}
              </Button>
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
