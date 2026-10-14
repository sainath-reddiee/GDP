"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ExternalLink, FileText, Link2, Loader2, Plus, RefreshCw, Trash2, Upload, Wand2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccess } from "@/components/access";
import {
  domainDocuments, domainSkills, linkConfluence, refreshDocument, removeDocument, saveDomainSkills, uploadDomainDocuments,
  type DomainDocuments, type DomainSkillRow, type DomainSkills,
} from "../actions";

type Msg = { tone: "ok" | "error"; text: string } | null;
const note = (m: Msg) => m && (
  <p role={m.tone === "error" ? "alert" : "status"} className={cn("rounded-lg px-3 py-2 text-xs", m.tone === "error" ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success")}>{m.text}</p>
);
const STAGE_LABEL = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();

/** The skills this domain's runs load, per stage: the standard's, plus the domain's own (added, or in place of one). */
function SkillsPanel({ domainId }: { domainId: string }) {
  const { canAct } = useAccess();
  const may = canAct("SKILL.RELEASE");
  const [data, setData] = useState<DomainSkills | null>(null);
  const [rows, setRows] = useState<DomainSkillRow[]>([]);
  const [adding, setAdding] = useState<{ stage: string; skill: string; replaces: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const load = () => void domainSkills(domainId).then((r) => {
    if (!r.ok) { setMsg({ tone: "error", text: r.error }); return; }
    setData(r.data);
    setRows(r.data.stages.flatMap((s) => s.domain_skills));
  });
  useEffect(load, [domainId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (next: DomainSkillRow[]) => {
    setBusy(true); setMsg(null);
    const r = await saveDomainSkills(domainId, next.map(({ stage, skill_name, mode, replaces, position, enabled, note }) =>
      ({ stage, skill_name, mode, replaces, position, enabled, note }))).catch(() => null);
    setBusy(false);
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Could not save." }); return; }
    setData(r.data); setRows(r.data.stages.flatMap((s) => s.domain_skills)); setAdding(null);
    setMsg({ tone: "ok", text: "Saved. This domain's next runs load these skills." });
  };
  if (!data) return <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Loading skills…</p>;
  return (
    <section className="surface space-y-3 p-4" data-testid="domain-skills">
      <div>
        <h3 className="text-sm font-semibold">Skills this domain&apos;s runs load</h3>
        <p className="text-xs text-muted-foreground">Per stage: the {data.standard} standard&apos;s skills, then this domain&apos;s own. A domain skill is added to the stage, or stands in for one of the standard&apos;s skills. Changing them needs SKILL.RELEASE.</p>
      </div>
      {!data.deployed && <p className="text-xs text-amber-600">Domain skills need migration V051. Until it is deployed, runs load the standard&apos;s skills only.</p>}
      {note(msg)}
      <ul className="divide-y divide-border/60">
        {data.stages.filter((s) => s.effective.length || rows.some((r) => r.stage === s.stage)).map((s) => {
          const mine = rows.filter((r) => r.stage === s.stage);
          return (
            <li key={s.stage} className="flex flex-wrap items-start gap-2 py-2 text-xs">
              <span className="w-24 shrink-0 pt-0.5 font-medium">{STAGE_LABEL(s.stage)}</span>
              <span className="flex min-w-0 flex-1 flex-wrap gap-1">
                {s.standard_skills.map((k) => {
                  const replaced = mine.find((m) => m.mode === "REPLACE" && (m.replaces ?? "").toUpperCase() === k.toUpperCase());
                  return (
                    <Link key={k} href={`/skills/${encodeURIComponent(k)}`} title={replaced ? `Replaced in this domain by ${replaced.skill_name}` : "From the standard"}
                          className={cn("rounded bg-muted px-1.5 py-0.5 font-mono hover:text-primary", replaced && "line-through opacity-60")}>{k}</Link>
                  );
                })}
                {mine.map((m) => (
                  <span key={m.skill_name} className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-primary" title={m.mode === "REPLACE" ? `In place of ${m.replaces}` : "Added for this domain"}>
                    {m.skill_name}{m.mode === "REPLACE" ? ` (for ${m.replaces})` : ""}
                    {may && <button type="button" aria-label={`Remove ${m.skill_name}`} disabled={busy} onClick={() => void save(rows.filter((r) => r !== m))}><X className="h-3 w-3" /></button>}
                  </span>
                ))}
              </span>
              {may && <button type="button" onClick={() => setAdding({ stage: s.stage, skill: "", replaces: "" })} className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-primary hover:bg-primary/10"><Plus className="h-3 w-3" /> Skill</button>}
              {adding?.stage === s.stage && (
                <div className="flex w-full flex-wrap items-center gap-2 pl-24">
                  <select aria-label="Skill" value={adding.skill} onChange={(e) => setAdding({ ...adding, skill: e.target.value })} className="h-8 rounded-lg border bg-background px-2 text-xs">
                    <option value="">Choose a skill…</option>
                    {data.available.map((a) => <option key={a.name} value={a.name} title={a.description}>{a.name}{a.version ? ` v${a.version}` : ""}</option>)}
                  </select>
                  <select aria-label="How" value={adding.replaces} onChange={(e) => setAdding({ ...adding, replaces: e.target.value })} className="h-8 rounded-lg border bg-background px-2 text-xs">
                    <option value="">Add to the stage</option>
                    {s.standard_skills.map((k) => <option key={k} value={k}>Instead of {k}</option>)}
                  </select>
                  <button type="button" disabled={!adding.skill || busy} onClick={() => void save([...rows, {
                    stage: s.stage, skill_name: adding.skill, mode: adding.replaces ? "REPLACE" : "ADD", replaces: adding.replaces || null,
                    position: 100 + mine.length, enabled: true, note: null }])}
                          className="h-8 rounded-lg bg-primary px-3 font-medium text-primary-foreground disabled:opacity-50">{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Attach"}</button>
                  <button type="button" onClick={() => setAdding(null)} className="h-8 rounded-lg px-2 hover:bg-muted">Cancel</button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Confluence pages and spaces linked to the domain, and uploaded documents: their knowledge is proposed for review. */
function DocumentsPanel({ domainId }: { domainId: string }) {
  const { canAct } = useAccess();
  const may = canAct("KNOWLEDGE.EDIT");
  const [data, setData] = useState<DomainDocuments | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = () => void domainDocuments(domainId).then((r) => (r.ok ? setData(r.data) : setMsg({ tone: "error", text: r.error })));
  useEffect(load, [domainId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const done = (text: string) => { setMsg({ tone: "ok", text }); load(); };
  const link = async () => {
    if (!url.trim()) return;
    setBusy("link"); setMsg(null);
    const r = await linkConfluence(domainId, url.trim()).catch(() => null);
    setBusy(null);
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Could not link the page." }); return; }
    setUrl("");
    done(`Linked ${r.data.title}: ${r.data.result.read} page${r.data.result.read === 1 ? "" : "s"} read, ${r.data.result.proposed} knowledge items proposed for review.`);
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    setBusy("upload"); setMsg(null);
    const r = await uploadDomainDocuments(domainId, form).catch(() => null);
    setBusy(null);
    if (file.current) file.current.value = "";
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Could not upload." }); return; }
    const bad = r.data.documents.filter((d) => d.error);
    done(r.data.documents.map((d) => d.error ? `${d.file_name}: ${d.error}` : `${d.file_name}: ${d.proposed ?? 0} items proposed`).join("; ")
      + (bad.length === r.data.documents.length ? "" : ". Review them in the knowledge inbox."));
  };
  const act = async (id: string, kind: "refresh" | "remove") => {
    setBusy(id); setMsg(null);
    const r = await (kind === "refresh" ? refreshDocument(domainId, id) : removeDocument(domainId, id)).catch(() => null);
    setBusy(null);
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Failed." }); return; }
    done(kind === "remove" ? "Unlinked. Knowledge already accepted from it stays." : (() => {
      const x = r.data as { read: number; unchanged: number; proposed: number };
      return `${x.read} changed page${x.read === 1 ? "" : "s"} read, ${x.unchanged} unchanged, ${x.proposed} items proposed.`;
    })());
  };
  if (!data) return <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Loading documents…</p>;
  const c = data.confluence;
  const canLink = may && c.enabled && c.connected;
  return (
    <section className="surface space-y-3 p-4" data-testid="domain-documents">
      <div>
        <h3 className="text-sm font-semibold">Documents</h3>
        <p className="text-xs text-muted-foreground">Confluence pages or spaces, and uploaded documents (an exported page, Word, Excel, Markdown, text). The AI proposes the knowledge they state, each with the passage it comes from; a steward accepts it in the <Link href="/knowledge?tab=inbox" className="text-primary hover:underline">review inbox</Link> before any stage uses it. A refresh re-reads only pages whose version changed.</p>
      </div>
      {!data.deployed && <p className="text-xs text-amber-600">Documents need migration V051.</p>}
      {note(msg)}
      {may && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-[18rem] flex-1 items-center gap-2">
            <input aria-label="Confluence link" value={url} onChange={(e) => setUrl(e.target.value)} disabled={!canLink || !!busy}
                   placeholder={!c.enabled ? "Confluence is not enabled (Admin, Integrations, Jira)" : !c.connected ? "Connect Jira on Tickets first" : "Paste a Confluence page or space link"}
                   className="h-9 min-w-0 flex-1 rounded-lg border bg-background px-3 text-sm disabled:opacity-60" />
            <button type="button" onClick={() => void link()} disabled={!canLink || !url.trim() || !!busy}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {busy === "link" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />} Link
            </button>
          </div>
          <input ref={file} type="file" multiple hidden accept=".md,.txt,.html,.htm,.docx,.xlsx,.csv,.json,.yml,.yaml,.sql" onChange={(e) => void upload(e.target.files)} />
          <button type="button" onClick={() => file.current?.click()} disabled={!!busy || !data.deployed}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm hover:bg-muted disabled:opacity-50">
            {busy === "upload" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} Upload
          </button>
        </div>
      )}
      {c.enabled && c.connected && !c.granted && <p className="text-xs text-amber-600">Your Jira sign-in does not include Confluence yet: connect Jira again on the Tickets page to grant it.</p>}
      {busy && <p className="text-xs text-muted-foreground"><Wand2 className="mr-1 inline h-3 w-3" />Reading and proposing knowledge; a long page or a space can take a minute.</p>}
      {!data.sources.length ? <p className="text-xs text-muted-foreground"><FileText className="mr-1 inline h-3.5 w-3.5" />No documents linked yet.</p> : (
        <ul className="divide-y divide-border/60">
          {data.sources.map((s) => (
            <li key={s.source_id} className="space-y-1 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", s.kind === "UPLOAD" ? "bg-cyan-500/10 text-cyan-700" : "bg-blue-500/10 text-blue-700")}>{s.kind === "UPLOAD" ? "Uploaded" : s.kind === "SPACE" ? "Confluence space" : "Confluence page"}</span>
                <span className="font-medium">{s.title ?? s.file_name}</span>
                {s.url && <a href={s.url} target="_blank" rel="noreferrer" className="text-muted-foreground hover:text-foreground" aria-label="Open in Confluence"><ExternalLink className="h-3 w-3" /></a>}
                <span className="text-muted-foreground">{s.last_refreshed_at ? `read ${s.last_refreshed_at}` : ""}{s.last_result?.proposed !== undefined ? ` · ${s.last_result.proposed} proposed` : ""}</span>
                {may && (
                  <span className="ml-auto flex gap-1">
                    {s.kind !== "UPLOAD" && <button type="button" onClick={() => void act(s.source_id, "refresh")} disabled={!!busy} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted"><RefreshCw className={cn("h-3 w-3", busy === s.source_id && "animate-spin")} /> Refresh</button>}
                    <button type="button" onClick={() => void act(s.source_id, "remove")} disabled={!!busy} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-3 w-3" /> Unlink</button>
                  </span>
                )}
              </div>
              {!!s.pages.length && (
                <ul className="space-y-0.5 pl-3 text-muted-foreground">
                  {s.pages.slice(0, 10).map((p) => <li key={p.page_id}>{p.title}{p.version ? ` · version ${p.version}` : ""} · {p.proposed} proposed · {(p.chars ?? 0).toLocaleString()} characters</li>)}
                  {s.pages.length > 10 && <li>+{s.pages.length - 10} more pages</li>}
                </ul>
              )}
              {!!s.last_result?.errors?.length && <p className="pl-3 text-destructive">{s.last_result.errors.length} page(s) could not be read: {s.last_result.errors[0].error}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The Sources tab: what this domain is defined by beyond its pack: its own skills and its documents. */
export function SourcesTab({ domainId }: { domainId: string }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DocumentsPanel domainId={domainId} />
      <SkillsPanel domainId={domainId} />
    </div>
  );
}
