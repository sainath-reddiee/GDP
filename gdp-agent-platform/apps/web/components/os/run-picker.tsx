"use client";

import { useRouter } from "next/navigation";
import { ChevronDown } from "lucide-react";

/** Switch the workbench to another run (keeps the page, changes ?run=). */
export function RunPicker({ base, current, runs }: { base: string; current: string; runs: { id: string; label: string; group: string }[] }) {
  const router = useRouter();
  const groups = Array.from(new Set(runs.map((r) => r.group)));
  return (
    <span className="relative inline-block max-w-full">
      <select value={current} aria-label="Workflow" onChange={(e) => router.push(`${base}?run=${e.target.value}`)}
              className="os-input max-w-[340px] appearance-none truncate py-1.5 pl-3 pr-8 text-[12px] font-semibold">
        {groups.map((g) => (
          <optgroup key={g} label={g}>
            {runs.filter((r) => r.group === g).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </optgroup>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 opacity-60" />
    </span>
  );
}
