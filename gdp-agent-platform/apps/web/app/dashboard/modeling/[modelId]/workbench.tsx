"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  AlertTriangle, CheckCircle2, Download, FileInput, GitBranch, History, Layers3, ListChecks, Loader2, MessageSquare, Network,
  GitPullRequest, PencilLine, RotateCcw, Ruler, Save, Send, ShieldCheck, Undo2, Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DmComment, DmDetail, DmIssue, DmSpec } from "@/lib/model-types";
import { sameSpec } from "@/lib/model-edit";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Modal } from "@/components/hub/overlay";
import { StatusPill, explain } from "@/components/hub/states";
import { ErCanvas } from "@/components/hub/er-canvas";
import { useToast } from "@/components/ui/toast";
import { importInto, openDraft, saveDraft, saveLayout, submitDraft, validateDraft, withdrawVersion } from "../model-actions";
import { EntitiesPanel, RelationshipsPanel } from "./entities-panel";
import { ReviewPanel } from "./review-panel";
import { StandardsPanel, VersionsPanel } from "./versions-panel";
import { ChangesPanel, PublishPanel } from "./publish-panel";

/** The tab in the address: after an action the page can remount with the props of the address it was loaded with,
 *  so the tab the user is on is read back from the address itself (on the server there is none). */
function rememberedTab(): string | null {
  try { return typeof window === "undefined" ? null : new URL(window.location.href).searchParams.get("tab"); } catch { return null; }
}

/** Keep the open tab in the address, so a refresh after an action (which can remount the page) stays on it. */
function rememberTab(tab: string) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState(window.history.state, "", url);
  } catch { /* the tab still switches */ }
}

type Tab = "diagram" | "entities" | "relationships" | "standards" | "versions" | "review" | "publish" | "changes";
const TABS: [Tab, string, typeof Network][] = [
  ["diagram", "Diagram", Network], ["entities", "Entities", Layers3], ["relationships", "Relationships", GitBranch],
  ["standards", "Standards", Ruler], ["versions", "Versions", History], ["review", "Review", MessageSquare],
  ["publish", "Publish", GitPullRequest], ["changes", "Proposals", Workflow],
];

function ImportModal({ modelId, spec, onClose, onLoaded }: { modelId: string; spec: DmSpec; onClose: () => void; onLoaded: (s: DmSpec, note: string) => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<"ddl" | "pack" | "schema">("ddl");
  const [text, setText] = useState("");
  const [database, setDatabase] = useState(spec.physical_defaults.database);
  const [schema, setSchema] = useState(spec.physical_defaults.schema);
  const [tables, setTables] = useState("");
  const [replace, setReplace] = useState(false);
  const [pending, start] = useTransition();
  const go = () => start(async () => {
    let pack: unknown;
    if (kind === "pack") {
      try { pack = JSON.parse(text); } catch { toast("error", "That is not valid JSON"); return; }
    }
    const r = await importInto(modelId, { kind, text: kind === "ddl" ? text : undefined, pack, database, schema,
      tables: tables.split(/[\s,]+/).filter(Boolean), replace, spec });
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    onLoaded(r.data.spec, `${r.data.entities} entit${r.data.entities === 1 ? "y" : "ies"} imported: ${r.data.counts.ADDITIVE} additive, ${r.data.counts.BREAKING} breaking, ${r.data.counts.NON_FUNCTIONAL} documentation change(s). Review and save the draft.`);
  });
  return (
    <Modal open onClose={onClose} wide title="Import into the draft"
           description="The import is merged into the draft as unsaved changes: same-named entities are updated and keep their ids and documentation, new ones are added. Nothing is saved until you save the draft."
           footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={go} disabled={pending || (kind !== "schema" && !text.trim()) || (kind === "schema" && (!database || !schema))} data-testid="run-import">
               {pending && <Loader2 className="h-4 w-4 animate-spin" />} Import</Button></>}>
      <div className="flex rounded-xl bg-muted p-1 text-xs">
        {([["ddl", "Snowflake DDL"], ["pack", "domain_pack.json"], ["schema", "Live schema"]] as const).map(([id, label]) => (
          <button key={id} type="button" onClick={() => setKind(id)} className={cn("flex-1 rounded-lg px-2.5 py-1 font-medium", kind === id ? "bg-card shadow-xs" : "text-muted-foreground")}>{label}</button>
        ))}
      </div>
      {kind !== "schema" ? (
        <><Label htmlFor="imp-text">{kind === "ddl" ? "CREATE TABLE statements" : "domain_pack.json"}</Label>
          <Textarea id="imp-text" rows={12} className="font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)}
                    placeholder={kind === "ddl" ? "CREATE TABLE SILVER.SALES.DIM_CUSTOMER (\n  CUSTOMER_ID NUMBER NOT NULL PRIMARY KEY,\n  ...\n);" : '{ "domain": {...}, "targets": [...] }'} /></>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          <div><Label htmlFor="imp-db">Database</Label><Input id="imp-db" className="font-mono" value={database} onChange={(e) => setDatabase(e.target.value.toUpperCase())} /></div>
          <div><Label htmlFor="imp-sch">Schema</Label><Input id="imp-sch" className="font-mono" value={schema} onChange={(e) => setSchema(e.target.value.toUpperCase())} /></div>
          <div className="sm:col-span-2"><Label htmlFor="imp-t">Tables (optional, comma separated)</Label><Input id="imp-t" className="font-mono" value={tables} onChange={(e) => setTables(e.target.value.toUpperCase())} placeholder="All tables of the schema" /></div>
        </div>
      )}
      <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
        Retire entities the import does not contain</label>
    </Modal>
  );
}

export function ModelWorkbench({ detail, comments, me, initialTab, canEdit, canApprove, approvedSpec }: {
  detail: DmDetail; comments: DmComment[]; me: string; initialTab: string | null; canEdit: boolean; canApprove: boolean;
  approvedSpec?: DmSpec | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const current = detail.current;
  const modelId = detail.model.model_id;
  const isOpen = !!current && detail.open?.version === current.version;
  const editable = canEdit && isOpen && current?.status === "DRAFT";
  const [spec, setSpec] = useState<DmSpec | null>(current?.spec ?? null);
  const [etag, setEtag] = useState(current?.etag ?? "");
  const [issues, setIssues] = useState<DmIssue[]>(current?.issues ?? []);
  const [tab, setTab] = useState<Tab>(() => (TABS.find(([t]) => t === (rememberedTab() ?? initialTab))?.[0]) ?? (current?.status === "IN_REVIEW" ? "review" : "diagram"));
  useEffect(() => rememberTab(tab), [tab]);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [conflict, setConflict] = useState("");
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState("");
  const [area, setArea] = useState("");
  const [pending, start] = useTransition();
  const layoutTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // a refresh after save brings a new server spec and etag
  useEffect(() => { setSpec(current?.spec ?? null); setEtag(current?.etag ?? ""); setIssues(current?.issues ?? []); }, [current?.etag, current?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = editable && !sameSpec(spec, current?.spec ?? null);
  const approvedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const e of approvedSpec?.design.entities ?? []) { ids.add(e.id); e.attributes.forEach((a) => ids.add(a.id)); }
    return ids;
  }, [approvedSpec]);
  const errors = issues.filter((i) => i.severity === "ERROR").length;
  const own = !!current && (current.authors.map((a) => a.toUpperCase()).includes(me) || (current.submitted_by ?? "").toUpperCase() === me);

  const open = (fromVersion?: number) => start(async () => {
    const r = await openDraft(modelId, fromVersion);
    if (r.ok) { toast("ok", `Draft v${r.data.version} opened`); router.push(`/dashboard/modeling/${modelId}?tab=entities`); router.refresh(); }
    else toast("error", explain(r.error).message);
  });
  const save = () => start(async () => {
    if (!spec) return;
    const r = await saveDraft(modelId, spec, etag, note);
    if (r.ok) {
      setEtag(r.data.etag); setIssues(r.data.issues); setConflict(""); setNotice("");
      toast("ok", `Draft saved: ${r.data.counts.ADDITIVE} additive, ${r.data.counts.BREAKING} breaking change(s) against the approved model`);
      router.refresh();
    } else if (/CONFLICT/i.test(r.error)) setConflict(explain(r.error).message.replace(/^CONFLICT:\s*/i, ""));
    else toast("error", explain(r.error).message);
  });
  const validate = () => start(async () => {
    if (!spec) return;
    const r = await validateDraft(modelId, spec);
    if (r.ok) { setIssues(r.data.issues); setTab("standards"); toast(r.data.issues.some((i) => i.severity === "ERROR") ? "error" : "ok", `${r.data.issues.length} issue(s)`); }
    else toast("error", explain(r.error).message);
  });
  const submit = () => start(async () => {
    const r = await submitDraft(modelId, note);
    if (r.ok) { toast("ok", `v${r.data.version} sent for review${r.data.breaking ? " (has breaking changes)" : ""}`); setNote(""); setTab("review"); router.refresh(); }
    else toast("error", explain(r.error).message);
  });
  const withdraw = () => start(async () => {
    const r = await withdrawVersion(modelId);
    if (r.ok) { toast("ok", "Taken out of review; keep editing"); router.refresh(); }
    else toast("error", explain(r.error).message);
  });
  const moveEnd = (positions: Record<string, { x: number; y: number }>) => {
    if (!canEdit || !spec) return;
    const byName = Object.fromEntries(spec.design.entities.map((e) => [e.entity_name, e.id]));
    const layout = Object.fromEntries(Object.entries(positions).filter(([n]) => byName[n]).map(([n, p]) => [byName[n], p]));
    if (layoutTimer.current) clearTimeout(layoutTimer.current);
    layoutTimer.current = setTimeout(() => { void saveLayout(modelId, { ...detail.model.diagram_layout, ...layout }); }, 800);
  };

  if (!current || !spec) {
    return <p className="text-sm text-muted-foreground">This model has no version yet.</p>;
  }
  const areas = Array.from(new Set(spec.design.entities.map((e) => e.subject_area).filter(Boolean))) as string[];
  const shownEntities = spec.design.entities.filter((e) => !area || e.subject_area === area);
  const names = new Set(shownEntities.map((e) => e.entity_name));
  const positions = Object.fromEntries(spec.design.entities.filter((e) => detail.model.diagram_layout[e.id]).map((e) => [e.entity_name, detail.model.diagram_layout[e.id]]));
  const selectedName = spec.design.entities.find((e) => e.id === selected)?.entity_name ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Version" className="h-9 w-64" value={String(current.version)}
                onChange={(e) => router.push(`/dashboard/modeling/${modelId}?version=${e.target.value}`)}>
          {detail.versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.status.toLowerCase().replace(/_/g, " ")}{v.version === detail.model.approved_version ? " (approved)" : ""}</option>)}
        </Select>
        {!detail.open && canEdit && (
          <Button onClick={() => open()} disabled={pending} data-testid="open-draft"><PencilLine className="h-4 w-4" /> Open a draft</Button>
        )}
        {detail.open && detail.open.version !== current.version && (
          <Link href={`/dashboard/modeling/${modelId}`} className="text-sm font-medium text-primary hover:underline">Go to v{detail.open.version} ({detail.open.status === "IN_REVIEW" ? "in review" : "draft"})</Link>
        )}
        {current.status === "IN_REVIEW" && own && <Button variant="outline" onClick={withdraw} disabled={pending}><Undo2 className="h-4 w-4" /> Withdraw to edit</Button>}
        {editable && <Button variant="outline" onClick={() => setImporting(true)} data-testid="import-model"><FileInput className="h-4 w-4" /> Import</Button>}
        <span className="ml-auto flex flex-wrap items-center gap-1 text-xs">
          <Download className="h-3.5 w-3.5 text-muted-foreground" />
          {(["ddl", "pack", "dbt", "csv"] as const).map((f) => (
            <a key={f} href={`/bff/model-export/${modelId}?format=${f}&version=${current.version}`} className="rounded-lg px-2 py-1 font-medium text-primary hover:bg-primary/10">
              {f === "ddl" ? "DDL" : f === "pack" ? "Domain pack" : f === "dbt" ? "dbt tests" : "Data dictionary"}</a>
          ))}
        </span>
      </div>

      {current.status === "IN_REVIEW" && !editable && (
        <p className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          <ShieldCheck className="h-4 w-4" /> v{current.version} is in review{current.submitted_by ? `, submitted by ${current.submitted_by}` : ""}. {own ? "Another model approver must decide it." : canApprove ? "Review it and decide in the Review tab." : "A model approver decides it."}
        </p>
      )}
      {current.status === "APPROVED" && !current.registry_synced_at && current.origin !== "BACKFILL" && (
        <p className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-100">
          <AlertTriangle className="h-4 w-4" /> Approved, but not applied to the domain registry yet. Apply it from the Review tab.
        </p>
      )}
      {conflict && (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-100" data-testid="conflict">
          <AlertTriangle className="h-4 w-4" /> {conflict}
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => { setConflict(""); router.refresh(); }}><RotateCcw className="h-3.5 w-3.5" /> Reload (your unsaved edits are lost)</Button>
        </div>
      )}
      {notice && <p className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100">{notice}</p>}

      <nav className="flex flex-wrap gap-1 border-b border-border/70" aria-label="Model sections">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined} data-testid={`model-tab-${id}`}
                  className={cn("relative flex items-center gap-1.5 px-3 pb-2 pt-1 text-sm transition", tab === id ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" /> {label}
            {id === "standards" && errors > 0 && <span className="rounded-full bg-rose-100 px-1.5 text-[10px] font-semibold text-rose-700">{errors}</span>}
            {id === "review" && comments.filter((c) => !c.resolved).length > 0 && <span className="rounded-full bg-muted px-1.5 text-[10px] font-semibold">{comments.filter((c) => !c.resolved).length}</span>}
            {tab === id && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-3 pb-2 text-xs text-muted-foreground">
          <span className="tabular-nums">{current.summary.entities} entities · {current.summary.columns} columns · {current.summary.relationships} relationships</span>
        </span>
      </nav>

      {tab === "diagram" && (
        <div className="space-y-2">
          {areas.length > 0 && (
            <div className="flex flex-wrap gap-1 text-xs">
              <button type="button" onClick={() => setArea("")} className={cn("rounded-full border px-2.5 py-0.5", !area ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>All subject areas</button>
              {areas.map((a) => <button key={a} type="button" onClick={() => setArea(a)} className={cn("rounded-full border px-2.5 py-0.5", area === a ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>{a}</button>)}
            </div>
          )}
          <ErCanvas className="h-[620px]" platform={spec.physical_defaults.platform} selected={selectedName}
                    onSelect={(name) => setSelected(spec.design.entities.find((e) => e.entity_name === name)?.id ?? null)}
                    entities={shownEntities.map((e) => ({ entity_name: e.entity_name, kind: e.table_type, purpose: e.description, grain: e.grain,
                      business_keys: e.business_keys, attributes: e.attributes.filter((a) => a.status !== "RETIRED") }))}
                    relationships={spec.relationships.filter((r) => names.has(r.from_entity) && names.has(r.to_entity))}
                    muted={new Set(spec.design.entities.filter((e) => e.status === "RETIRED").map((e) => e.entity_name))}
                    positions={positions} onMoveEnd={moveEnd} />
          <p className="text-xs text-muted-foreground">Drag tables to arrange them (saved for everyone, no review needed). Dashed lines are optional relationships; faded tables are retired. Double-check details in Entities.</p>
        </div>
      )}
      {tab === "entities" && (
        <EntitiesPanel spec={spec} onChange={setSpec} editable={editable} selected={selected} onSelect={setSelected} approvedIds={approvedIds}
                       issues={issues} approvedSpec={approvedSpec ?? null} />
      )}
      {tab === "relationships" && <RelationshipsPanel spec={spec} onChange={setSpec} editable={editable} />}
      {tab === "standards" && (
        <StandardsPanel modelId={modelId} issues={issues} standard={detail.model.naming_standard} canEdit={canEdit}
                        onPick={(path) => { const e = spec.design.entities.find((x) => x.entity_name === path.split(".")[0]); if (e) { setSelected(e.id); setTab("entities"); } }} />
      )}
      {tab === "versions" && <VersionsPanel modelId={modelId} versions={detail.versions} approved={detail.model.approved_version}
                                            canRollback={canEdit && !detail.open} onRollback={(v) => open(v)} />}
      {tab === "review" && (
        <ReviewPanel modelId={modelId} version={current} approvedVersion={detail.model.approved_version} comments={comments} me={me}
                     canComment={canEdit || canApprove} canDecide={canApprove && !own && current.status === "IN_REVIEW"} own={own} dirty={dirty} />
      )}

      {tab === "publish" && <PublishPanel modelId={modelId} versions={detail.versions} canPublish={canEdit} />}
      {tab === "changes" && <ChangesPanel modelId={modelId} canEdit={canEdit} openStatus={detail.open?.status ?? null} />}

      {editable && (
        <div className="sticky bottom-4 z-30 mx-auto flex w-full max-w-4xl flex-wrap items-center gap-2 rounded-2xl border bg-card p-2.5 shadow-pop" data-testid="model-bar">
          <span className="flex items-center gap-2 pl-2 text-sm font-medium">
            <span className={cn("h-2 w-2 rounded-full", dirty ? "bg-warning" : "bg-success")} />
            {dirty ? "Unsaved changes" : `Draft v${current.version} saved`}
            {errors > 0 && <span className="rounded-full bg-rose-100 px-2 text-[11px] font-semibold text-rose-700">{errors} error{errors === 1 ? "" : "s"}</span>}
          </span>
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed and why (shown to reviewers)" className="h-8 min-w-[200px] flex-1 text-xs" aria-label="Change note" />
          {dirty && <Button size="sm" variant="ghost" onClick={() => { setSpec(current.spec); setNotice(""); }}><RotateCcw className="h-3.5 w-3.5" /> Discard</Button>}
          <Button size="sm" variant="outline" onClick={validate} disabled={pending}><ListChecks className="h-3.5 w-3.5" /> Validate</Button>
          <Button size="sm" variant={dirty ? "default" : "outline"} onClick={save} disabled={pending || !dirty} data-testid="save-draft">
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save draft</Button>
          <Button size="sm" onClick={submit} disabled={pending || dirty || errors > 0} title={dirty ? "Save the draft first" : errors ? "Fix the errors first" : ""} data-testid="submit-review">
            <Send className="h-3.5 w-3.5" /> Submit for review</Button>
        </div>
      )}
      {!editable && current.status === "APPROVED" && current.version === detail.model.approved_version && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground"><CheckCircle2 className="h-3.5 w-3.5 text-success" />
          This is the approved model{current.approved_by ? `, approved by ${current.approved_by}` : ""}. {canEdit && !detail.open ? "Open a draft to change it." : ""}</p>
      )}
      {importing && <ImportModal modelId={modelId} spec={spec} onClose={() => setImporting(false)}
                                 onLoaded={(s, msg) => { setSpec(s); setImporting(false); setNotice(msg); setTab("entities"); }} />}
    </div>
  );
}
