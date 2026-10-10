import Link from "next/link";
import { FileCode2 } from "lucide-react";

export type CodeCitation = {
  repo: string | null; repo_id: string | null; path: string | null; lines: string | null; kind: string | null;
  name: string | null; commit: string | null; chunk_id?: string | null;
};

/** "Code used": the client repository snippets an AI step was given, each linking to the Code page. */
export function CodeCitations({ items, label = "Code used" }: { items?: CodeCitation[] | null; label?: string }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
      {items.map((c) => {
        const start = (c.lines ?? "").split("-")[0];
        const href = `/code?repo=${encodeURIComponent(c.repo_id ?? "")}&path=${encodeURIComponent(c.path ?? "")}${start ? `&line=${start}` : ""}`;
        return (
          <Link key={`${c.repo_id}-${c.path}-${c.lines}`} href={href} title={`${c.repo}:${c.path} lines ${c.lines}${c.commit ? ` @ ${c.commit}` : ""}`}
                className="inline-flex max-w-[260px] items-center gap-1 rounded-full border border-indigo-100 bg-indigo-50/70 px-2 py-0.5 font-mono text-[10px] text-indigo-700 hover:border-indigo-300">
            <FileCode2 className="h-3 w-3 shrink-0" />
            <span className="truncate">{(c.path ?? "").split("/").slice(-2).join("/")}:{start}</span>
          </Link>
        );
      })}
    </div>
  );
}
