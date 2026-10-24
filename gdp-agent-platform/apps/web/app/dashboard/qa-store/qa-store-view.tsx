"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { BrainCircuit, FileCode2, FolderGit2, Loader2, Plus, Search, SquareTerminal, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PackRow, PackSeverity } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { SqlEditor, TagChip } from "@/components/quality/sql-editor";
import { useToast } from "@/components/ui/toast";
import { createPack, importPack, type Imported } from "./actions";
import { PackDrawer } from "./pack-drawer";

type Domain = { domain_id: string; domain_name: string };
const STATUS_FILTERS = [
  { id: "", label: "All" }, { id: "APPROVED", label: "Approved" }, { id: "DRAFT", label: "Drafts" },
  { id: "PENDING_APPROVAL", label: "Pending" }, { id: "RETIRED", label: "Retired" },
] as const;

export const splitTags = (v: string) => v.split(/[,\s]+/).map((t) => t.replace(/^#/, "").trim().toLowerCase()).filter(Boolean);

function LastRunBar({ run }: { run: PackRow["last_run"] }) {
  if (!run || !run.tests) return <span className="text-xs text-muted-foreground">never run</span>;
  const pass = (100 * run.passed) / run.tests;
  return (
    <span className="flex items-center gap-2" title={`${run.passed} of ${run.tests} passed · ${run.started_at.slice(0, 16)}`}>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-rose-100">
        <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${pass}%` }} />
      </span>
      <span className="text-xs tabular-nums text-muted-foreground">{run.passed}/{run.tests}</span>
    </span>
  );
}

function PackCard({ pack, onOpen }: { pack: PackRow; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} data-testid={`pack-${pack.name}`}
            className="surface group flex flex-col p-4 text-left transition duration-200 hover:-translate-y-px hover:border-primary/30 hover:shadow-hover">
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary text-white shadow-sm shadow-blue-500/30">
          <SquareTerminal className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{pack.name}</p>
          <p className="text-xs text-muted-foreground">
            v{pack.version}{pack.approved_version && pack.approved_version !== pack.version ? ` · approved v${pack.approved_version}` : ""} · {pack.queries} quer{pack.queries === 1 ? "y" : "ies"}
          </p>
        </div>
        <StatusPill status={pack.status} />
      </div>
      {pack.repo_conflict && <p className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">The repository file changed since the app edits; open to resolve</p>}
      {pack.description && <p className="mt-3 line-clamp-2 text-sm text-muted-foreground">{pack.description}</p>}
      {pack.tags.length > 0 && <div className="mt-3 flex flex-wrap gap-1">{pack.tags.slice(0, 5).map((t) => <TagChip key={t} tag={t} />)}</div>}
      <div className="min-h-3 flex-1" />
      <div className="flex items-center gap-3 border-t border-border/60 pt-3 text-xs text-muted-foreground">
        <LastRunBar run={pack.last_run} />
        <span className="ml-auto flex items-center gap-2">
          {pack.source === "REPO" && <span className="flex items-center gap-1" title="Seeded from domain/<name>/qa in Git"><FolderGit2 className="h-3.5 w-3.5" /> repo</span>}
          {pack.approved_version && pack.status !== "RETIRED" && (
            <span className="flex items-center gap-1 text-blue-600" title="The approved queries are QA_TEST knowledge: Cortex Search and the QA prompts read them">
              <BrainCircuit className="h-3.5 w-3.5" /> AI context
            </span>
          )}
        </span>
      </div>
    </button>
  );
}

function NewPackModal({ open, onClose, domains, scope, onCreated }: {
  open: boolean; onClose: () => void; domains: Domain[]; scope: string; onCreated: (packId: string) => void;
}) {
  const initial = domains.find((d) => d.domain_name.toUpperCase() === scope.toUpperCase())?.domain_id ?? domains[0]?.domain_id ?? "";
  const [domainId, setDomainId] = useState(initial);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [queryName, setQueryName] = useState("orphan_keys");
  const [severity, setSeverity] = useState<PackSeverity>("ERROR");
  const [sql, setSql] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const toast = useToast();
  const submit = () => start(async () => {
    setError("");
    const r = await createPack(domainId, {
      name, description: description || null, tags: splitTags(tags), change_note: "Created",
      queries: [{ query_name: queryName, description: null, sql_expression: sql, severity, expectation: "0 rows", tags: [] }],
    });
    if (r.ok) { toast("ok", `Created ${name} as a draft`); onCreated(r.data.pack_id); }
    else if (r.pending) { toast("info", r.error); onClose(); }
    else setError(explain(r.error).message);
  });
  return (
    <Modal open={open} onClose={onClose} wide title="New QA pack"
           description="A pack groups the assertions one domain owns. It starts as a draft; submit it for approval to make it live."
           footer={<>
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={submit} disabled={pending || !domainId || !name.trim() || !queryName.trim() || sql.trim().length < 6}>
               {pending && <Loader2 className="h-4 w-4 animate-spin" />} Create draft
             </Button>
           </>}>
      <div className="grid gap-x-4 sm:grid-cols-2">
        <div><Label htmlFor="np-domain">Domain</Label>
          <Select id="np-domain" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
            {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
          </Select></div>
        <div><Label htmlFor="np-name">Pack name</Label><Input id="np-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Referential integrity" /></div>
        <div className="sm:col-span-2"><Label htmlFor="np-desc">Description</Label>
          <Textarea id="np-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What this pack guarantees" /></div>
        <div className="sm:col-span-2"><Label htmlFor="np-tags">Tags</Label>
          <Input id="np-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="reconciliation, referential-integrity, sla" /></div>
      </div>
      <div className="mt-5 rounded-xl border border-border/70 bg-muted/30 p-3">
        <p className="text-sm font-medium">First query</p>
        <p className="text-xs text-muted-foreground">Write a query that returns the rows that break the rule. It passes when it returns 0 rows.</p>
        <div className="grid gap-x-4 sm:grid-cols-[1fr_160px]">
          <div><Label htmlFor="np-q">Query name</Label><Input id="np-q" value={queryName} onChange={(e) => setQueryName(e.target.value)} /></div>
          <div><Label htmlFor="np-sev">Severity</Label>
            <Select id="np-sev" value={severity} onChange={(e) => setSeverity(e.target.value as PackSeverity)}>
              <option value="ERROR">Error</option><option value="WARN">Warn</option><option value="INFO">Info</option>
            </Select></div>
        </div>
        <div className="mt-3"><SqlEditor value={sql} onChange={setSql} rows={7} /></div>
      </div>
      {error && <p className="mt-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p>}
    </Modal>
  );
}

/** Domain-grouped pack cards with search, status and tag filters; a card opens the pack drawer. */
/** Bring existing SQL in: a .sql file becomes a draft pack, each query guarded as read-only against the domain's
 *  tables. Statements that are not queries are left out and listed. */
function ImportPackModal({ onClose, domains, scope, onImported }: {
  onClose: () => void; domains: Domain[]; scope: string; onImported: (packId: string) => void;
}) {
  const initial = domains.find((d) => d.domain_name.toUpperCase() === scope.toUpperCase())?.domain_id ?? domains[0]?.domain_id ?? "";
  const [domainId, setDomainId] = useState(initial);
  const [name, setName] = useState("");
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Imported | null>(null);
  const [pending, start] = useTransition();
  const ref = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const router = useRouter();
  const close = () => { onClose(); if (done) router.refresh(); };
  const pick = async (f: File | undefined) => {
    setError("");
    if (!f) return;
    if (f.size > 500_000) { setError(`${f.name} is larger than 500 KB`); return; }
    setFile({ name: f.name, text: await f.text() });
  };
  const submit = () => start(async () => {
    if (!file) return;
    setError("");
    const r = await importPack(domainId, { filename: file.name, text: file.text, name: name.trim() || null });
    if (r.ok) { setDone(r.data); toast("ok", `Imported ${r.data.queries} quer${r.data.queries === 1 ? "y" : "ies"} as a draft`); }
    else if (r.pending) { toast("info", r.error); onClose(); }
    else setError(explain(r.error).message);
  });
  return (
    <Modal open onClose={close} title="Import SQL into a QA pack"
           description="Each query should return the rows that break a rule; it passes when it returns none. The pack starts as a draft."
           footer={done ? <>
             <Button variant="ghost" onClick={close}>Close</Button>
             <Button onClick={() => onImported(done.pack_id)}>Open the pack</Button>
           </> : <>
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={submit} disabled={pending || !domainId || !file}>{pending && <Loader2 className="h-4 w-4 animate-spin" />} Import</Button>
           </>}>
      {done ? (
        <div className="space-y-3" data-testid="qa-import-done">
          <p className="text-sm"><span className="font-semibold">{done.name}</span> was created as a draft with {done.queries} quer{done.queries === 1 ? "y" : "ies"}. Run it, then submit it for approval.</p>
          {done.skipped.length > 0 && (
            <div className="rounded-lg border border-dashed p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Left out ({done.skipped.length})</p>
              <ul className="mt-1.5 space-y-1 text-xs text-muted-foreground">{done.skipped.map((s) => <li key={s}>{s}</li>)}</ul>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="grid gap-x-4 sm:grid-cols-2">
            <div><Label htmlFor="ip-domain">Domain</Label>
              <Select id="ip-domain" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </Select></div>
            <div><Label htmlFor="ip-name">Pack name</Label>
              <Input id="ip-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={file ? "from the file" : "Customer integrity"} /></div>
          </div>
          <button type="button" onClick={() => ref.current?.click()} data-testid="qa-import-pick"
                  className="mt-4 flex w-full items-center gap-3 rounded-xl border border-dashed border-border p-4 text-left transition hover:border-primary/40 hover:bg-accent/30">
            <FileCode2 className="h-6 w-6 text-primary" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{file?.name ?? "Choose a .sql file"}</span>
              <span className="block text-xs text-muted-foreground">
                {file ? `${file.text.split(";").filter((s) => s.trim()).length} statement(s) found; click to replace` : "Plain queries separated by ; (a comment above a query names it), or a pack exported from here"}
              </span>
            </span>
            <Upload className="h-4 w-4 text-muted-foreground" />
          </button>
          <input ref={ref} type="file" accept=".sql,text/plain" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
          {error && <p className="mt-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p>}
        </>
      )}
    </Modal>
  );
}

export function QaStoreView({ packs, domains, scope, initialPack, openNew, canEdit, canApprove, canRun }: {
  packs: PackRow[]; domains: Domain[]; scope: string; initialPack: string | null; openNew: boolean;
  canEdit: boolean; canApprove: boolean; canRun: boolean;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [open, setOpen] = useState<string | null>(initialPack);
  const [creating, setCreating] = useState(openNew && canEdit);
  const [importing, setImporting] = useState(false);
  useEffect(() => { if (openNew && canEdit) setCreating(true); }, [openNew, canEdit]);

  const tags = useMemo(() => {
    const n: Record<string, number> = {};
    packs.forEach((p) => p.tags.forEach((t) => { n[t] = (n[t] ?? 0) + 1; }));
    return Object.entries(n).sort((a, b) => b[1] - a[1]).map(([t]) => t).slice(0, 14);
  }, [packs]);
  const shown = packs.filter((p) => (!status || p.status === status) && (!tag || p.tags.includes(tag))
    && (!q || `${p.name} ${p.description ?? ""} ${p.domain_name ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  const groups = shown.reduce<Record<string, PackRow[]>>((g, p) => { (g[p.domain_name ?? "No domain"] ??= []).push(p); return g; }, {});

  const close = () => { setOpen(null); if (initialPack) router.replace("/os/design/qa-store"); };

  return (
    <div className="space-y-5">
      <div className="glass flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search packs" className="pl-9" aria-label="Search packs" />
        </div>
        <div role="radiogroup" aria-label="Status" className="flex rounded-xl bg-muted p-1">
          {STATUS_FILTERS.map((s) => (
            <button key={s.id} type="button" role="radio" aria-checked={status === s.id} onClick={() => setStatus(s.id)}
                    className={cn("rounded-lg px-3 py-1.5 text-xs font-medium transition", status === s.id ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
              {s.label}
            </button>
          ))}
        </div>
        {canEdit && <Button variant="outline" onClick={() => setImporting(true)} disabled={!domains.length} data-testid="qa-import"><Upload className="h-4 w-4" /> Import .sql</Button>}
        {canEdit && <Button onClick={() => setCreating(true)} disabled={!domains.length}><Plus className="h-4 w-4" /> New pack</Button>}
        {tags.length > 0 && (
          <div className="flex w-full flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs text-muted-foreground">Tags</span>
            {tags.map((t) => <TagChip key={t} tag={t} active={tag === t} onClick={() => setTag(tag === t ? "" : t)} />)}
          </div>
        )}
      </div>

      {!packs.length ? (
        <EmptyState icon={SquareTerminal} title="No QA packs yet"
                    action={canEdit ? <Button onClick={() => setCreating(true)} disabled={!domains.length}><Plus className="h-4 w-4" /> New pack</Button> : undefined}>
          Packs hold the deterministic checks a domain owns. Create one here, or commit a file under domain/&lt;name&gt;/qa/ and deploy to seed it.
        </EmptyState>
      ) : !shown.length ? (
        <p className="py-10 text-center text-sm text-muted-foreground">No pack matches these filters.</p>
      ) : Object.entries(groups).map(([domain, list]) => (
        <section key={domain} className="space-y-3">
          <div className="flex items-baseline gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.08em] text-muted-foreground">{domain}</h2>
            <span className="text-xs text-muted-foreground">{list.length} pack{list.length === 1 ? "" : "s"}</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {list.map((p) => <PackCard key={p.pack_id} pack={p} onOpen={() => setOpen(p.pack_id)} />)}
          </div>
        </section>
      ))}

      {creating && (
        <NewPackModal open onClose={() => setCreating(false)} domains={domains} scope={scope}
                      onCreated={(id) => { setCreating(false); setOpen(id); router.refresh(); }} />
      )}
      {importing && (
        <ImportPackModal onClose={() => setImporting(false)} domains={domains} scope={scope}
                         onImported={(id) => { setImporting(false); setOpen(id); router.refresh(); }} />
      )}
      {open && <PackDrawer packId={open} onClose={close} canEdit={canEdit} canApprove={canApprove} canRun={canRun} />}
    </div>
  );
}
