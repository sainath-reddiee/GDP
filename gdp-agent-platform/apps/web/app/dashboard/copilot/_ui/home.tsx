"use client";

import type { ReactNode } from "react";
import { BookOpen, Database, FileSpreadsheet, GitBranch, ShieldCheck, Ticket } from "lucide-react";

const STARTERS: { icon: typeof Database; title: string; prompt: string }[] = [
  { icon: Database, title: "Onboard a source", prompt: "Onboard a new source through SnapLogic. Ask me what you need." },
  { icon: Ticket, title: "Pick up a Jira issue", prompt: "Pick up my Jira issue and draft the changes it needs." },
  { icon: GitBranch, title: "Change a model", prompt: "Add a column to a table model, with its mapping and checks." },
  { icon: ShieldCheck, title: "Add quality checks", prompt: "Draft data quality checks and QA queries for a table." },
  { icon: FileSpreadsheet, title: "Work from a file", prompt: "I attached a requirements file. Read it and propose the changes." },
  { icon: BookOpen, title: "Ask the domain", prompt: "How is customer lifetime value defined in our domain?" },
];

/** The empty state: what the copilot does, with starters that fill the composer. */
export function Home({ composer, onStarter, disabled }: { composer: ReactNode; onStarter: (prompt: string) => void; disabled?: string }) {
  return (
    <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-6 py-10">
     <div className="my-auto flex flex-col gap-8">
      <div className="space-y-3 text-center">
        <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-primary text-white shadow-lg shadow-blue-500/25">
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
            <path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z" strokeLinejoin="round" />
            <path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" strokeLinejoin="round" />
          </svg>
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">What should we build?</h1>
        <p className="mx-auto max-w-xl text-sm text-muted-foreground">
          Describe the change, attach a requirements file or pick a Jira issue. The copilot reads your domain, asks only
          what it cannot find, and drafts every change for your review. Nothing is saved until you approve it.
        </p>
      </div>
      {composer}
      {disabled && <p role="status" className="text-center text-xs text-muted-foreground">{disabled}</p>}
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {STARTERS.map(({ icon: Icon, title, prompt }) => (
          <button key={title} type="button" disabled={!!disabled} onClick={() => onStarter(prompt)}
                  className="group flex items-start gap-3 rounded-xl border border-border/80 bg-card p-3 text-left shadow-xs transition hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-card disabled:pointer-events-none disabled:opacity-50">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Icon className="h-4 w-4" /></span>
            <span className="min-w-0">
              <span className="block text-sm font-medium">{title}</span>
              <span className="line-clamp-2 text-xs text-muted-foreground">{prompt}</span>
            </span>
          </button>
        ))}
      </div>
     </div>
    </div>
  );
}
