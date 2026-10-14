import { Fragment, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Inline markdown: `code` and **bold** only; everything else is plain text (no HTML is ever rendered). */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

/** A markdown document (headings, paragraphs, lists, tables, code blocks, rules) as readable blocks. Headings get ids
 *  (`md-<slug>`) so a reader can jump to a section. */
export function MarkdownDoc({ text, className, highlight }: { text: string; className?: string; highlight?: (heading: string) => boolean }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      out.push(<pre key={out.length} className="overflow-x-auto rounded-lg border bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed text-slate-100">{body.join("\n")}</pre>);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1].length, title = h[2].trim();
      const id = `md-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
      const tone = highlight?.(title) ? "border-l-4 border-primary pl-2" : "";
      out.push(level <= 1 ? <h2 key={out.length} id={id} className={cn("pt-2 text-lg font-semibold", tone)}>{inline(title)}</h2>
        : level === 2 ? <h3 key={out.length} id={id} className={cn("pt-3 text-base font-semibold", tone)}>{inline(title)}</h3>
          : <h4 key={out.length} id={id} className={cn("pt-2 text-sm font-semibold", tone)}>{inline(title)}</h4>);
      i++;
      continue;
    }
    if (/^-{3,}\s*$/.test(line)) { out.push(<hr key={out.length} className="my-2 border-border" />); i++; continue; }
    if (line.trim().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      const head = cells(rows[0]);
      const body = rows.slice(1).filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map(cells);
      out.push(
        <div key={out.length} className="overflow-x-auto rounded-lg border">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 text-left"><tr>{head.map((c, k) => <th key={k} className="px-2 py-1.5 font-medium">{inline(c)}</th>)}</tr></thead>
            <tbody>{body.map((r, k) => <tr key={k} className="border-t">{r.map((c, j) => <td key={j} className="px-2 py-1 align-top">{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const List = ordered ? "ol" : "ul";
      out.push(<List key={out.length} className={cn("space-y-0.5 pl-5 text-sm", ordered ? "list-decimal" : "list-disc")}>{items.map((t, k) => <li key={k}>{inline(t)}</li>)}</List>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\||\s*([-*]|\d+\.)\s|-{3,}\s*$)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) { para.push(lines[i++]); }
    out.push(<p key={out.length} className="text-sm leading-relaxed">{inline(para.join(" "))}</p>);
  }
  return <div className={cn("space-y-2", className)}>{out}</div>;
}
