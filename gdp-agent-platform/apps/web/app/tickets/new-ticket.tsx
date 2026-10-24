"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Bot, Check, ExternalLink, Loader2 } from "lucide-react";
import { Modal } from "@/components/hub/overlay";
import { Input, Textarea } from "@/components/ui/input";
import { createJiraIssue, jiraIssueTypes, jiraProjects, type JiraProject } from "../jira/actions";
import { sprintChoices, type SprintChoice } from "./actions";

const field = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary/15";

/** Create a Jira issue in the person's own Jira account; then open it, or hand it to the Copilot. */
export function NewTicket({ open, onClose, defaultProject, onCreated }: {
  open: boolean; onClose: () => void; defaultProject: string | null; onCreated: (key: string) => void;
}) {
  const [projects, setProjects] = useState<JiraProject[] | null>(null);
  const [project, setProject] = useState(defaultProject ?? "");
  const [types, setTypes] = useState<string[]>([]);
  const [type, setType] = useState("Task");
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [labels, setLabels] = useState("");
  const [sprints, setSprints] = useState<SprintChoice[]>([]);
  const [sprint, setSprint] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ key: string; url: string; existing: boolean } | null>(null);
  const idem = useRef(crypto.randomUUID());

  useEffect(() => {
    if (!open || projects) return;
    void jiraProjects().then((r) => {
      if (!r.ok) { setError(r.error); setProjects([]); return; }
      setProjects(r.data.projects);
      setProject((p) => p || r.data.default || r.data.projects[0]?.key || "");
    });
  }, [open, projects]);

  useEffect(() => {   // the project's active and future sprints (none: no Jira Software, or a kanban board)
    setSprints([]); setSprint("");
    if (!project || !open) return;
    let live = true;
    void sprintChoices(project).then((r) => { if (live && r.ok && r.data.sprint_field) setSprints(r.data.sprints); });
    return () => { live = false; };
  }, [project, open]);

  useEffect(() => {
    if (!project) { setTypes([]); return; }
    let live = true;
    void jiraIssueTypes(project).then((r) => {
      if (!live) return;
      const names = r.ok ? r.data.types.filter((t) => !t.subtask).map((t) => t.name) : [];
      setTypes(names);
      setType((t) => (names.includes(t) ? t : names.find((n) => n === "Task") ?? names[0] ?? ""));
    });
    return () => { live = false; };
  }, [project]);

  const reset = () => {
    setSummary(""); setDescription(""); setLabels(""); setError(null); setDone(null); idem.current = crypto.randomUUID();
  };
  const close = () => { if (!busy) { reset(); onClose(); } };

  const submit = async () => {
    if (!project || !type || !summary.trim() || busy) return;
    setBusy(true); setError(null);
    const r = await createJiraIssue({
      project_key: project, issue_type: type, summary: summary.trim(), description_markdown: description,
      labels: labels.split(",").map((l) => l.trim()).filter(Boolean), idempotency_key: idem.current,
      sprint_id: sprint ? Number(sprint) : null,
    }).catch(() => null);
    setBusy(false);
    if (!r) { setError("The ticket could not be created. Try again."); return; }
    if (!r.ok) { setError(r.error); return; }
    const data = r.data as typeof r.data & { pending_approval?: boolean; detail?: string };
    if (data.pending_approval) { setError(data.detail ?? "Creating tickets needs approval; the request is in Approvals."); return; }
    setDone({ key: data.key, url: data.url, existing: data.existing });
  };

  return (
    <Modal open={open} onClose={close} title="New Jira ticket"
           description="Created in your own Jira account. Describe the work; the Copilot can pick it up from there."
           footer={done ? (
             <>
               <a href={done.url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm hover:bg-muted">
                 Open in Jira <ExternalLink className="h-3.5 w-3.5" />
               </a>
               <button type="button" onClick={() => { onCreated(done.key); close(); }} className="h-9 rounded-lg border px-3 text-sm hover:bg-muted">View ticket</button>
               <Link href={`/os/work/copilot?jira=${encodeURIComponent(done.key)}`}
                     className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground">
                 <Bot className="h-4 w-4" /> Work on it in the Copilot
               </Link>
             </>
           ) : (
             <>
               <button type="button" onClick={close} disabled={busy} className="h-9 rounded-lg px-3 text-sm hover:bg-muted">Cancel</button>
               <button type="button" onClick={() => void submit()} disabled={busy || !project || !type || !summary.trim()}
                       className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
                 {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Create ticket
               </button>
             </>
           )}>
      {done ? (
        <p className="text-sm" role="status">
          {done.existing ? "This ticket was already created: " : "Created "}
          <a href={done.url} target="_blank" rel="noreferrer" className="font-mono font-semibold text-primary hover:underline">{done.key}</a>.
        </p>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">Project
              <select aria-label="Project" value={project} onChange={(e) => setProject(e.target.value)} className={field} disabled={!projects}>
                {!projects && <option value="">Loading…</option>}
                {projects?.map((p) => <option key={p.key} value={p.key}>{p.key} · {p.name}</option>)}
                {projects && !projects.length && <option value="">No project you can create in</option>}
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium">Type
              <select aria-label="Type" value={type} onChange={(e) => setType(e.target.value)} className={field} disabled={!types.length}>
                {types.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>
          {sprints.length > 0 && (
            <label className="block space-y-1 text-xs font-medium">Sprint <span className="font-normal text-muted-foreground">(optional)</span>
              <select aria-label="Sprint" value={sprint} onChange={(e) => setSprint(e.target.value)} className={field}>
                <option value="">Backlog (no sprint)</option>
                {sprints.map((s) => <option key={s.id} value={String(s.id)}>{s.name} ({s.state}) · {s.board}</option>)}
              </select>
            </label>
          )}
          <label className="block space-y-1 text-xs font-medium">Summary
            <Input aria-label="Summary" value={summary} maxLength={255} onChange={(e) => setSummary(e.target.value)} placeholder="Add LOYALTY_TIER to DIM_CUSTOMER" />
          </label>
          <label className="block space-y-1 text-xs font-medium">Description <span className="font-normal text-muted-foreground">(markdown)</span>
            <Textarea aria-label="Description" value={description} rows={6} maxLength={30000} onChange={(e) => setDescription(e.target.value)}
                      placeholder="What is needed, the source and target tables, acceptance criteria." />
          </label>
          <label className="block space-y-1 text-xs font-medium">Labels <span className="font-normal text-muted-foreground">(comma separated, optional)</span>
            <Input aria-label="Labels" value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="gdp, data-model" />
          </label>
          {error && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">{error}</p>}
        </div>
      )}
    </Modal>
  );
}
