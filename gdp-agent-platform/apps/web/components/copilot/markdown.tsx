"use client";

import { Fragment, useState } from "react";
import { Check, Copy } from "lucide-react";

function Inline({ text }: { text: string }) {
  // `code`, **bold**; everything else is plain text (no HTML from the model is ever rendered)
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => p.startsWith("`") && p.endsWith("`") && p.length > 2
        ? <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{p.slice(1, -1)}</code>
        : p.startsWith("**") && p.endsWith("**") && p.length > 4
          ? <strong key={i}>{p.slice(2, -2)}</strong>
          : <Fragment key={i}>{p}</Fragment>)}
    </>
  );
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <div className="flex items-center justify-between rounded-t-lg border border-b-0 bg-muted/70 px-2.5 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        <span>{lang || "code"}</span>
        <button type="button" onClick={() => navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}
                className="flex items-center gap-1 hover:text-foreground">
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}{copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto rounded-b-lg border bg-muted/40 p-2.5 font-mono text-[11px] leading-relaxed">{code}</pre>
    </div>
  );
}

/** A small, safe Markdown subset for copilot answers: paragraphs, bullet and numbered lists, headings, fenced code. */
export function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```\s*([\w-]*)/);
    if (fence) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i += 1;
      blocks.push(<CodeBlock key={blocks.length} lang={fence[1]} code={body.join("\n")} />);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={blocks.length} className={ordered ? "list-decimal space-y-0.5 pl-5" : "list-disc space-y-0.5 pl-5"}>
          {items.map((it, j) => <li key={j}><Inline text={it} /></li>)}
        </List>,
      );
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.*)/);
    if (heading) {
      blocks.push(<p key={blocks.length} className="font-semibold"><Inline text={heading[1]} /></p>);
      i += 1;
      continue;
    }
    if (!line.trim()) { i += 1; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^```|^\s*([-*]|\d+\.)\s+|^#{1,4}\s+/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={blocks.length}><Inline text={para.join(" ")} /></p>);
  }
  return <div className="space-y-2 text-sm leading-relaxed">{blocks}</div>;
}
