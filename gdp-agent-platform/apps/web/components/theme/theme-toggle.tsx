"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme, type ThemeChoice } from "./theme-provider";

const OPTIONS: { value: ThemeChoice; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

/** One button that cycles light, dark and system (the top bar). */
export function ThemeToggle({ className }: { className?: string }) {
  const { choice, resolved, setChoice } = useTheme();
  const next = OPTIONS[(OPTIONS.findIndex((o) => o.value === choice) + 1) % OPTIONS.length];
  const Icon = choice === "system" ? Monitor : resolved === "dark" ? Moon : Sun;
  return (
    <button type="button" onClick={() => setChoice(next.value)} data-testid="theme-toggle"
            title={`Theme: ${choice}. Switch to ${next.label.toLowerCase()}`} aria-label={`Theme: ${choice}. Switch to ${next.label.toLowerCase()}`}
            className={cn("grid h-9 w-9 place-items-center rounded-xl text-muted-foreground transition hover:bg-muted hover:text-foreground", className)}>
      <Icon className="h-[18px] w-[18px]" />
    </button>
  );
}

/** Segmented light, dark, system picker (the account menu). */
export function ThemeSegmented() {
  const { choice, setChoice } = useTheme();
  return (
    <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-1 rounded-xl bg-muted p-1">
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button key={value} type="button" role="radio" aria-checked={choice === value} onClick={() => setChoice(value)}
                className={cn("flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition",
                  choice === value ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
          <Icon className="h-3.5 w-3.5" /> {label}
        </button>
      ))}
    </div>
  );
}
