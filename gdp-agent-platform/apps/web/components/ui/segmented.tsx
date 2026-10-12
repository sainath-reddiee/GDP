"use client";

import { cn } from "@/lib/utils";

/** A small segmented switch (radio group) for filters and view toggles. */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border bg-muted/40 p-0.5">
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
                className={cn("rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                  value === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
          {text}
        </button>
      ))}
    </div>
  );
}
