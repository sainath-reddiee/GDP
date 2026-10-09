"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

/** [op, old line, new line, text]; op is " ", "-", "+" or "…" (then text is the number of hidden lines). */
export type DiffLine = [string, number | null, number | null, string | number];
export type DiffFile = { path: string; status: "added" | "removed" | "changed" | "same"; added: number; removed: number; ops: DiffLine[] };

const STATUS: Record<DiffFile["status"], string> = {
  added: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  removed: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  changed: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  same: "bg-muted text-muted-foreground",
};

/** Unified, per-file diff computed on the server (unchanged files collapsed). */
export function DiffView({ files }: { files: DiffFile[] }) {
  const changed = files.filter((f) => f.status !== "same");
  const same = files.length - changed.length;
  if (!changed.length) return <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">The two versions are identical.</p>;
  return (
    <div className="space-y-3">
      {changed.map((f) => <FileDiff key={f.path} file={f} />)}
      {same > 0 && <p className="text-xs text-muted-foreground">{same} unchanged file{same === 1 ? "" : "s"} hidden.</p>}
    </div>
  );
}

function FileDiff({ file }: { file: DiffFile }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="overflow-hidden rounded-xl border">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="flex w-full items-center gap-2 border-b bg-muted/40 px-3 py-2 text-left text-xs">
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono">{file.path}</span>
        <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", STATUS[file.status])}>{file.status}</span>
        <span className="ml-auto font-mono"><span className="text-emerald-600">+{file.added}</span> <span className="text-red-600">-{file.removed}</span></span>
      </button>
      {open && (
        <div className="max-h-[520px] overflow-auto overscroll-contain">
          <table className="w-full border-collapse font-mono text-[11px] leading-5">
            <tbody>
              {file.ops.map((op, i) => op[0] === "…" ? (
                <tr key={i} className="bg-sky-50/60 text-sky-700 dark:bg-sky-950/30 dark:text-sky-300">
                  <td colSpan={3} className="px-3 py-0.5 text-[10px]">⋯ {op[3]} unchanged line{op[3] === 1 ? "" : "s"}</td>
                </tr>
              ) : (
                <tr key={i} className={cn(op[0] === "+" && "bg-emerald-50 dark:bg-emerald-950/40", op[0] === "-" && "bg-red-50 dark:bg-red-950/40")}>
                  <td className="w-10 select-none border-r px-2 text-right text-muted-foreground/70">{op[1] ?? ""}</td>
                  <td className="w-10 select-none border-r px-2 text-right text-muted-foreground/70">{op[2] ?? ""}</td>
                  <td className="whitespace-pre-wrap break-all px-2">
                    <span className={cn("mr-2 select-none", op[0] === "+" ? "text-emerald-600" : op[0] === "-" ? "text-red-600" : "text-transparent")}>{op[0] === " " ? "·" : op[0]}</span>
                    {op[3]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
