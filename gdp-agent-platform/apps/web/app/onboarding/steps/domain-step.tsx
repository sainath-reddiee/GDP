"use client";

import { AlertTriangle, CheckCircle2, GitMerge, Layers, Loader2, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import type { DomainRelation, DomainRow } from "../intent-types";
import { cn } from "@/lib/utils";

export type ModelOption = {
  fqn: string; target_table: string; domain_name?: string | null;
  score?: number; overlap_columns?: string[]; reason?: string;
};

export function DomainStep({
  relation, setRelation, related, checking, domains, domainId, setDomainId,
  newDomainMode, setNewDomainMode, newDomain, setNewDomain,
  options, selected, toggleTarget,
}: {
  relation: DomainRelation | "";
  setRelation: (v: DomainRelation) => void;
  related: boolean | null;
  checking: boolean;
  domains: DomainRow[];
  domainId: string;
  setDomainId: (v: string) => void;
  newDomainMode: "create" | "existing";
  setNewDomainMode: (v: "create" | "existing") => void;
  newDomain: { domain_name: string; description: string };
  setNewDomain: (v: { domain_name: string; description: string }) => void;
  options: ModelOption[];
  selected: string[];
  toggleTarget: (fqn: string) => void;
}) {
  const recommendation = related == null ? null : related ? "existing_domain" : "new_domain";
  const disagrees = relation && recommendation && relation !== recommendation;
  const duplicateName = newDomain.domain_name.trim()
    && domains.some((d) => d.domain_name.toLowerCase() === newDomain.domain_name.trim().toLowerCase());

  const choice = (value: DomainRelation, icon: typeof GitMerge, title: string, body: string) => {
    const Icon = icon;
    const active = relation === value;
    return (
      <button
        type="button"
        aria-pressed={active}
        onClick={() => setRelation(value)}
        className={cn(
          "relative flex flex-1 items-start gap-3 rounded-xl border p-4 text-left transition-all",
          active ? "border-primary bg-primary/5 ring-2 ring-primary/30" : "hover:border-foreground/30 hover:bg-muted/40",
        )}
      >
        <span className={cn("rounded-lg p-2", active ? "bg-primary text-primary-foreground" : "bg-muted")}><Icon className="h-5 w-5" /></span>
        <span className="min-w-0">
          <span className="flex items-center gap-2 text-sm font-semibold">
            {title}
            {recommendation === value && <Badge variant="outline" className="text-[10px]"><Sparkles className="mr-1 h-3 w-3" />suggested</Badge>}
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{body}</span>
        </span>
      </button>
    );
  };

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm font-medium">Is this source related to an existing domain or model?</p>
        <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
          {checking ? (
            <><Loader2 className="h-3 w-3 animate-spin" /> Comparing source columns with registered models…</>
          ) : related == null ? (
            "Pick tables first so we can compare them with registered models."
          ) : related ? (
            <><CheckCircle2 className="h-3 w-3 text-emerald-600" /> Found {options.length} registered model{options.length === 1 ? "" : "s"} that overlap these tables.</>
          ) : (
            "No registered model overlaps these tables strongly."
          )}
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          {choice("existing_domain", GitMerge, "Yes, map to existing models", "Profile the source, then map it to the target models you choose.")}
          {choice("new_domain", Layers, "No, it's a new source", "Profile the source, then let the agents propose a target model.")}
        </div>
        {disagrees && (
          <p className="mt-2 flex items-start gap-1.5 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {relation === "existing_domain"
              ? "Column overlap with registered models is low. Mapping may need many manual decisions."
              : "These tables overlap existing models. Proposing a new model could duplicate one."}
            {" "}Your choice is kept.
          </p>
        )}
      </div>

      {relation === "existing_domain" && (
        <div className="space-y-3 rounded-xl border bg-muted/20 p-4">
          <div className="max-w-sm">
            <Label htmlFor="domain" className="mt-0">Domain</Label>
            <Select id="domain" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
              <option value="">All domains</option>
              {domains.map((d) => (
                <option key={d.domain_id} value={d.domain_id}>
                  {d.domain_name}{d.target_tables != null ? ` (${d.target_tables} models)` : ""}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <div className="mb-2 flex items-center gap-2">
              <p className="text-sm font-medium">Target models</p>
              <span className="text-xs text-muted-foreground">{selected.length} selected</span>
            </div>
            {options.length === 0 ? (
              <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                This domain has no matching models yet. Switch to <b>new source</b> to profile and propose one.
              </p>
            ) : (
              <div className="grid max-h-72 gap-2 overflow-y-auto sm:grid-cols-2">
                {options.map((o) => {
                  const on = selected.includes(o.fqn);
                  const pct = o.score != null ? Math.round(o.score * 100) : null;
                  return (
                    <label key={o.fqn} className={cn("flex cursor-pointer items-start gap-2 rounded-lg border p-3", on ? "border-primary bg-primary/5" : "hover:bg-muted/40")}>
                      <input type="checkbox" className="mt-1" checked={on} onChange={() => toggleTarget(o.fqn)} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm font-semibold">{o.target_table}</span>
                          {pct != null && (
                            <span className={cn("ml-auto rounded-full px-1.5 text-[10px] font-medium",
                              pct >= 60 ? "bg-emerald-100 text-emerald-700" : pct >= 30 ? "bg-amber-100 text-amber-700" : "bg-muted text-muted-foreground")}>
                              {pct}% match
                            </span>
                          )}
                        </span>
                        <span className="block truncate font-mono text-[11px] text-muted-foreground">{o.fqn}</span>
                        {o.overlap_columns?.length ? (
                          <span className="mt-1 block truncate text-[11px] text-muted-foreground">shares {o.overlap_columns.slice(0, 5).join(", ")}{o.overlap_columns.length > 5 ? "…" : ""}</span>
                        ) : o.reason ? <span className="mt-1 block text-[11px] text-muted-foreground">{o.reason}</span> : null}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {relation === "new_domain" && (
        <div className="space-y-3 rounded-xl border bg-muted/20 p-4">
          <p className="text-sm font-medium">Which domain does it belong to?</p>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2"><input type="radio" name="nd" checked={newDomainMode === "create"} onChange={() => setNewDomainMode("create")} /> Create a new domain</label>
            <label className="flex items-center gap-2"><input type="radio" name="nd" checked={newDomainMode === "existing"} onChange={() => setNewDomainMode("existing")} disabled={!domains.length} /> Existing domain (new model)</label>
          </div>
          {newDomainMode === "create" ? (
            <div className="grid gap-x-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="nd_name">Domain name</Label>
                <Input id="nd_name" value={newDomain.domain_name} onChange={(e) => setNewDomain({ ...newDomain, domain_name: e.target.value })} placeholder="Customer" maxLength={128} />
                {duplicateName && <p className="mt-1 text-xs text-amber-700">A domain with this name exists, so it will be reused.</p>}
              </div>
              <div className="sm:col-span-2">
                <Label htmlFor="nd_desc">Description</Label>
                <Textarea id="nd_desc" value={newDomain.description} onChange={(e) => setNewDomain({ ...newDomain, description: e.target.value })} placeholder="Entities, owners and key business terms" maxLength={4000} />
              </div>
            </div>
          ) : (
            <div className="max-w-sm">
              <Select value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                <option value="">Choose domain…</option>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </Select>
            </div>
          )}
          <p className="text-xs text-muted-foreground">Next the agents will profile → suggest a model → you review it.</p>
        </div>
      )}
    </div>
  );
}
