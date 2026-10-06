"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { Check, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CatalogExplorer } from "@/components/catalog-explorer";
import {
  catalogRelatedTarget, defaultMapExistingFqns, displayDomain, isHiddenTarget, isModelTable,
  localModelSuggestions, registryTargetOptions, sourceSystemName, targetFqn,
} from "@/lib/catalog-display";
import { cn } from "@/lib/utils";
import { createRun } from "./actions";
import { createDomain, loadCatalogSuggestions, loadColumns, loadSchemas, loadTables, previewGraph } from "./catalog";
import type { DatabaseRow, SchemaRow, TableRow, TargetRow } from "./catalog-types";
import type {
  DomainRelation, DomainRow, ModelGraph, OnboardingIntent, SourceDetails, SourceOrigin,
} from "./intent-types";
import { DomainStep, type ModelOption } from "./steps/domain-step";
import { ReviewStep, type CheckItem } from "./steps/review-step";
import { EXTERNAL_CONNECTORS, SourceStep } from "./steps/source-step";

type Suggestion = {
  kind: "existing" | "proposed"; target_table: string; fqn: string;
  domain_name?: string | null; score: number; overlap_columns: string[]; reason: string;
};

const STEPS = [
  { title: "Source", body: "Origin and details" },
  { title: "Catalog", body: "Database, schema, tables" },
  { title: "Domain & models", body: "Existing or new" },
  { title: "Review", body: "ER diagram and plan" },
];
const MAX_PREVIEW_TABLES = 60;

function downloadJson(name: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

const sameDomain = (a?: string | null, b?: string | null) =>
  (displayDomain(a) || "").toLowerCase() === (displayDomain(b) || "").toLowerCase();

export function OnboardingForm({
  databases, targets, domains,
}: {
  databases: DatabaseRow[];
  targets: TargetRow[];
  domains: DomainRow[];
}) {
  const [step, setStep] = useState(0);
  const [runName, setRunName] = useState("");
  const [origin, setOrigin] = useState<SourceOrigin | "">("");
  const [details, setDetails] = useState<SourceDetails>({});

  const [database, setDatabase] = useState("");
  const [sourceType, setSourceType] = useState("SNOWFLAKE_DATABASE");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<TableRow[]>([]);
  const [pickedTables, setPickedTables] = useState<string[]>([]);
  const [loading, setLoading] = useState<"schemas" | "tables" | null>(null);

  const [relation, setRelation] = useState<DomainRelation | "">("");
  const [domainId, setDomainId] = useState("");
  const [newDomainMode, setNewDomainMode] = useState<"create" | "existing">("create");
  const [newDomain, setNewDomain] = useState({ domain_name: "", description: "" });
  const [selectedTargets, setSelectedTargets] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [scopedTargets, setScopedTargets] = useState<TargetRow[]>([]);
  const [related, setRelated] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);

  const [graph, setGraph] = useState<ModelGraph | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [, load] = useTransition();

  const visibleTargets = useMemo(() => targets.filter((t) => !isHiddenTarget(t)), [targets]);
  const sourceTables = useMemo(() => tables.filter((t) => !isModelTable(t.table_name)), [tables]);
  const effectiveTables = pickedTables.length ? pickedTables : sourceTables.map((t) => t.table_name);
  const external = origin === "external";
  const chosenDomain = domains.find((d) => d.domain_id === domainId);

  // compare the selected tables with registered models; drives the "related?" recommendation
  useEffect(() => {
    if (external || !database || !schema) {
      setSuggestions([]); setScopedTargets([]); setRelated(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    const timer = setTimeout(async () => {
      try {
        const result = await loadCatalogSuggestions(database, schema, pickedTables);
        if (cancelled) return;
        const next = (result.suggestions || []).filter((s) => s.kind === "existing" && !isHiddenTarget(s));
        const registry = (result.targets?.length
          ? result.targets
          : visibleTargets.filter((t) => catalogRelatedTarget(database, schema, t))
        ).filter((t) => !isHiddenTarget(t));
        setSuggestions(next);
        setScopedTargets(registry);
        setRelated(Boolean(result.related) || localModelSuggestions(database, schema, tables, pickedTables).some((s) => s.score > 0.5));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not suggest models");
      } finally {
        if (!cancelled) setChecking(false);
      }
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [external, database, schema, pickedTables.join("|"), tables.length]);

  const allOptions: ModelOption[] = useMemo(() => {
    const domainRegistry = visibleTargets.filter((t) => chosenDomain && sameDomain(t.domain_name, chosenDomain.domain_name));
    return [
      ...suggestions,
      ...(database && schema ? localModelSuggestions(database, schema, tables, pickedTables) : []),
      ...registryTargetOptions(scopedTargets),
      ...registryTargetOptions(domainRegistry).map((o) => ({ ...o, score: undefined, reason: "Model in this domain" })),
    ].filter((s, i, all) => all.findIndex((x) => x.fqn === s.fqn) === i);
  }, [suggestions, database, schema, tables, pickedTables, scopedTargets, visibleTargets, chosenDomain]);

  const options = useMemo(() => {
    const scoped = chosenDomain
      ? allOptions.filter((o) => !o.domain_name || sameDomain(o.domain_name, chosenDomain.domain_name))
      : allOptions;
    return [...scoped].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }, [allOptions, chosenDomain]);

  const chooseRelation = (next: DomainRelation) => {
    setRelation(next);
    if (next === "new_domain") {
      setSelectedTargets([]);
      if (!domains.length) setNewDomainMode("create");
      return;
    }
    if (!selectedTargets.length && database && schema) {
      setSelectedTargets(defaultMapExistingFqns(database, schema, tables, pickedTables, scopedTargets, suggestions));
    }
  };

  const resetDownstream = () => {
    setPickedTables([]); setSelectedTargets([]); setGraph(null); setRelated(null);
  };

  const pickDatabase = (name: string, type: string) => {
    if (name === database) return;
    setDatabase(name);
    setSourceType(type === "IMPORTED DATABASE" ? "SNOWFLAKE_SHARE" : "SNOWFLAKE_DATABASE");
    setSchema(""); setTables([]); setSchemas([]);
    resetDownstream();
    setLoading("schemas");
    load(async () => {
      try {
        setSchemas((await loadSchemas(name)).schemas);
      } catch (e) {
        setError(e instanceof Error ? `${e.message} — try a role with USAGE on ${name}.` : "Could not list schemas");
      } finally {
        setLoading(null);
      }
    });
  };

  const pickSchema = (name: string) => {
    if (name === schema) return;
    setSchema(name);
    resetDownstream();
    setLoading("tables");
    load(async () => {
      try {
        setTables((await loadTables(database, name)).tables);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not list tables");
      } finally {
        setLoading(null);
      }
    });
  };

  const toggleTable = (name: string) =>
    setPickedTables((prev) => prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name]);
  const toggleTarget = (fqn: string) =>
    setSelectedTargets((prev) => prev.includes(fqn) ? prev.filter((t) => t !== fqn) : [...prev, fqn]);

  const refreshGraph = useCallback(() => {
    if (external || !database || !schema) return;
    setGraphLoading(true);
    setGraphError("");
    previewGraph(database, schema, effectiveTables.slice(0, MAX_PREVIEW_TABLES), relation === "existing_domain" ? selectedTargets : [])
      .then(setGraph)
      .catch((e) => setGraphError(e instanceof Error ? e.message : "Could not build the ER preview"))
      .finally(() => setGraphLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [external, database, schema, effectiveTables.join("|"), relation, selectedTargets.join("|")]);

  useEffect(() => { if (step === 3) refreshGraph(); }, [step, refreshGraph]);

  const path = relation === "existing_domain" ? "map_existing" : "profile_suggest";

  const intent = (domainOverride?: DomainRow | null): OnboardingIntent => {
    const chosen = selectedTargets.map((fqn) => {
      const registered = visibleTargets.find((t) => targetFqn(t) === fqn);
      const suggested = allOptions.find((s) => s.fqn === fqn);
      return {
        fqn,
        target_table: registered?.target_table || suggested?.target_table || fqn.split(".").pop() || fqn,
        domain_name: displayDomain(registered?.domain_name || suggested?.domain_name) || "",
        target_table_id: registered?.target_table_id,
      };
    });
    const fromTargets = visibleTargets.find((t) => selectedTargets.includes(targetFqn(t)))?.domain_name || null;
    const domain = domainOverride
      || chosenDomain
      || (relation === "existing_domain" ? domains.find((d) => d.domain_name === fromTargets) : undefined)
      || null;
    return {
      path,
      run_name: runName.trim(),
      source: {
        origin: origin || "snowflake",
        database, schema,
        source_system_name: sourceSystemName(database, schema),
        source_type: external ? `EXTERNAL_${(details.external_connector || "UNKNOWN").toUpperCase()}` : sourceType,
        tables: pickedTables,
        details,
      },
      relation: relation || undefined,
      domain_id: domain?.domain_id ?? null,
      domain_name: displayDomain(domain?.domain_name || fromTargets),
      new_domain: relation === "new_domain" && newDomainMode === "create" && newDomain.domain_name.trim()
        ? { domain_name: newDomain.domain_name.trim(), description: newDomain.description.trim() || undefined }
        : null,
      targets: chosen,
      model_existing: path === "map_existing",
      created_at: new Date().toISOString(),
    };
  };

  // gates and edge cases
  const stepReady = [
    Boolean(runName.trim() && origin && (!external || details.external_connector)),
    Boolean(database && schema && sourceTables.length),
    relation === "existing_domain"
      ? selectedTargets.length > 0
      : relation === "new_domain"
        ? (newDomainMode === "create" ? newDomain.domain_name.trim().length >= 2 : Boolean(domainId))
        : false,
    true,
  ];
  const canVisit = (i: number) => !external ? stepReady.slice(0, i).every(Boolean) : i === 0;

  const warnings: string[] = [];
  if (!pickedTables.length && sourceTables.length) {
    warnings.push(`No tables picked, so all ${sourceTables.length} source tables in ${schema} will be onboarded.`);
  }
  if (effectiveTables.length > MAX_PREVIEW_TABLES) {
    warnings.push(`The ER preview shows the first ${MAX_PREVIEW_TABLES} tables. The run still includes all ${effectiveTables.length}.`);
  }
  if (graph?.isolated?.length) {
    warnings.push(`No join keys found for ${graph.isolated.join(", ")}. Confirm they are independent, or join them in the mapping step.`);
  }
  const empty = sourceTables.filter((t) => effectiveTables.includes(t.table_name) && t.row_count === 0).map((t) => t.table_name);
  if (empty.length) warnings.push(`${empty.slice(0, 5).join(", ")}${empty.length > 5 ? "…" : ""} are empty, so profiling will be limited.`);
  if (sourceType === "SNOWFLAKE_SHARE") warnings.push("Source is a read-only share. Profiling uses sampled queries and nothing is written back.");
  if (relation === "existing_domain" && selectedTargets.length > 1 && effectiveTables.length > 1) {
    warnings.push("Several sources map to several targets. Each target gets its own mapping review.");
  }
  if (graph && relation === "existing_domain" && graph.edges.every((e) => !e.weight)) {
    warnings.push("Selected models share no column names with the source. Mapping will rely on semantic matching.");
  }

  const checks: CheckItem[] = [
    { label: "Run named", ok: Boolean(runName.trim()) },
    { label: "Source in Snowflake", ok: origin === "snowflake", hint: "External sources must land in Snowflake first." },
    { label: "Catalog and schema chosen", ok: Boolean(database && schema) },
    { label: "Source tables available", ok: sourceTables.length > 0 },
    { label: relation === "existing_domain" ? "Target models selected" : "Domain chosen", ok: stepReady[2] },
    { label: "ER preview built", ok: Boolean(graph), hint: "Open this step to generate it." },
  ];

  const canCreate = !external && stepReady.slice(0, 3).every(Boolean) && !pending;

  const submit = () => start(async () => {
    setError("");
    let domain: DomainRow | null = null;
    if (relation === "new_domain" && newDomainMode === "create") {
      try {
        domain = (await createDomain(newDomain.domain_name.trim(), newDomain.description.trim())).domain;
      } catch (e) {
        setError(e instanceof Error ? `Could not create domain: ${e.message}` : "Could not create domain");
        return;
      }
    }
    const result = await createRun(intent(domain));
    if (result && !result.ok) setError(result.error);
  });

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 pb-6">
      <div>
        <h1 className="text-xl font-semibold">New onboarding</h1>
        <p className="text-sm text-muted-foreground">Tell us where the source lives, pick its tables, then decide whether it maps to an existing model or needs a new one.</p>
      </div>

      <ol className="grid gap-2 sm:grid-cols-4">
        {STEPS.map((s, i) => {
          const done = i < step && stepReady[i];
          const enabled = canVisit(i);
          return (
            <li key={s.title}>
              <button
                type="button"
                disabled={!enabled}
                onClick={() => setStep(i)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  i === step ? "border-primary bg-primary/5" : "bg-card hover:bg-muted/40",
                )}
              >
                <span className={cn(
                  "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  done ? "border-emerald-600 bg-emerald-600 text-white" : i === step ? "border-primary bg-primary text-primary-foreground" : "",
                )}>
                  {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{s.title}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">{s.body}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      <Card className="overflow-hidden">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">{STEPS[step].title}</CardTitle>
          <CardDescription>
            {step === 0 && "Is this a Snowflake source or an external system? Add the basics."}
            {step === 1 && "Browse catalogs, preview columns and pick the tables to onboard."}
            {step === 2 && "Human check: does this source belong to an existing domain or model?"}
            {step === 3 && "How the source tables join, and how they flow into target models."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {step === 0 && (
            <SourceStep
              runName={runName} setRunName={setRunName}
              origin={origin} setOrigin={setOrigin}
              details={details} setDetails={setDetails}
            />
          )}
          {step === 1 && (
            <CatalogExplorer
              databases={databases}
              schemas={schemas}
              tables={tables}
              database={database}
              schema={schema}
              picked={pickedTables}
              loading={loading}
              defaultKind="share"
              onDatabase={pickDatabase}
              onSchema={pickSchema}
              onToggleTable={toggleTable}
              onPickAll={(names) => setPickedTables((prev) => [...new Set([...prev, ...names])])}
              onClearTables={() => setPickedTables([])}
              loadColumns={async (table) => (await loadColumns(database, schema, table)).columns}
              hideTable={isModelTable}
            />
          )}
          {step === 2 && (
            <DomainStep
              relation={relation} setRelation={chooseRelation}
              related={related} checking={checking}
              domains={domains} domainId={domainId} setDomainId={setDomainId}
              newDomainMode={newDomainMode} setNewDomainMode={setNewDomainMode}
              newDomain={newDomain} setNewDomain={setNewDomain}
              options={options} selected={selectedTargets} toggleTarget={toggleTarget}
            />
          )}
          {step === 3 && (
            <ReviewStep
              graph={graph}
              loading={graphLoading}
              error={graphError}
              onRefresh={refreshGraph}
              checks={checks}
              warnings={warnings}
              runName={runName}
              pathLabel={relation === "existing_domain" ? "Profile + map" : "Profile + suggest"}
            />
          )}
        </CardContent>

        <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t bg-card px-6 py-3">
          {error && <p role="alert" className="w-full text-sm text-destructive">{error}</p>}
          <Button type="button" variant="outline" size="sm" disabled={step === 0} onClick={() => setStep(step - 1)}>
            <ChevronLeft className="mr-1 h-4 w-4" /> Back
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!runName.trim()}
            onClick={() => downloadJson(`${(runName || "onboarding-plan").trim().replace(/\s+/g, "-")}.json`, { ...intent(), graph })}
          >
            Download plan
          </Button>
          <span className="ml-auto text-xs text-muted-foreground">
            {external
              ? `External (${EXTERNAL_CONNECTORS.find((c) => c.id === details.external_connector)?.label || "pick a connector"}): save the plan; runs need the data in Snowflake.`
              : !stepReady[step] && step < 3
                ? ["Name the run and choose where the source lives.", "Pick a catalog and schema that have source tables.", "Answer the domain question and finish the selection.", ""][step]
                : ""}
          </span>
          {step < 3 ? (
            <Button type="button" size="sm" disabled={external || !stepReady[step]} onClick={() => setStep(step + 1)}>
              Next <ChevronRight className="ml-1 h-4 w-4" />
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={!canCreate} onClick={submit}>
              {pending ? "Creating…" : "Create run"}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}
