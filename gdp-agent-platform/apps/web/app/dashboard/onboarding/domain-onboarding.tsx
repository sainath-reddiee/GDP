"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, FileText, PenTool, Upload } from "lucide-react";
import { PackEditor } from "@/app/domains/pack-editor";
import { Formats, TemplateViewer } from "./domain-formats";
import { ChoiceStep, RailStep, Reveal, type Choice } from "./rail";
import { DomainImportCard } from "./tiles";

type How = "import" | "draft" | "design";

const HOW: Choice<How>[] = [
  { key: "import", label: "Import a pack", icon: Upload },
  { key: "draft", label: "Draft from a contract", icon: FileText },
  { key: "design", label: "Design a model", icon: PenTool },
];

const TITLE: Record<How, string> = { import: "Upload the pack and contract", draft: "Paste the contract", design: "Design on the canvas" };

/** Steps 2 and 3 for a domain: how the domain model arrives, then that one path, each with the formats it takes. */
export function DomainOnboarding({ initial, openImport, domains }: {
  initial: How | null; openImport: boolean; domains: { domain_id: string; domain_name: string; label: string }[];
}) {
  const [how, setHow] = useState<How | null>(initial);
  // a link to a step while already on this page (the "+ New" menu) changes only the query, so follow it
  useEffect(() => { if (initial) setHow(initial); }, [initial]);
  const choose = (h: How | null) => {
    setHow(h);
    const url = new URL(window.location.href);
    url.searchParams.delete("import");
    if (h) url.searchParams.set("how", h); else url.searchParams.delete("how");
    window.history.replaceState(window.history.state, "", url);
  };
  return (
    <>
      <ChoiceStep n={2} title="How the domain arrives" choices={HOW} value={how} onChange={choose} testId="onboard-domain" />
      {how && (
        <Reveal key={how}>
          <RailStep n={3} title={TITLE[how]} last>
            {how === "import" && (
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
                <div className="space-y-3">
                  <DomainImportCard openImport={openImport} />
                  <Accepts label="Accepted" items={["domain_pack.json", "contract .yaml / .yml", "contract .md"]}
                           text="Start from the templates: they pass the same checks the import runs." />
                </div>
                <TemplateViewer />
              </div>
            )}
            {how === "draft" && (
              <div className="space-y-3">
                <Accepts label="Paste or upload" items={["yaml", "md", "txt", "sql", "csv", "json"]}
                         text="Up to 200,000 characters: a data contract, model document, DDL or spreadsheet export. AI drafts a pack you review before anything is saved." />
                <PackEditor domains={domains} embedded />
              </div>
            )}
            {how === "design" && (
              <div className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <DesignLink href="/dashboard/modeling?view=tables&new=1" title="New table model" text="Columns, keys and types, published to the domain" />
                  <DesignLink href="/dashboard/modeling" title="Modeling Store" text="Every model on a canvas, with its relationships" />
                </div>
                <Accepts label="Or import a model" items={["Erwin .xml", ".sql DDL", ".xlsx", ".csv", "pack .json"]}
                         text="Up to 25 MB, in the Model Workbench. Changes are merged into a draft that is reviewed before any run uses it." />
              </div>
            )}
          </RailStep>
        </Reveal>
      )}
    </>
  );
}

function Accepts({ label, items, text }: { label: string; items: string[]; text: string }) {
  return (
    <div className="rounded-lg border border-dashed px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
        <Formats items={items} />
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">{text}</p>
    </div>
  );
}

function DesignLink({ href, title, text }: { href: string; title: string; text: string }) {
  return (
    <Link href={href} className="group flex items-center gap-3 rounded-lg border bg-card px-4 py-3 transition hover:border-primary/40 hover:bg-accent/40">
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{title}</span>
        <span className="block text-xs text-muted-foreground">{text}</span>
      </span>
      <ArrowUpRight className="h-4 w-4 text-muted-foreground transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary" />
    </Link>
  );
}
