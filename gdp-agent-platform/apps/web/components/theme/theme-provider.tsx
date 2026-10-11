"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export const THEME_KEY = "aip.theme";

/** Runs before the first paint (inline in the root layout) so a dark page never flashes light. */
export const THEME_SCRIPT = `(function(){try{var c=localStorage.getItem("${THEME_KEY}")||"system";var d=c==="dark"||(c==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;

type Ctx = { choice: ThemeChoice; resolved: "light" | "dark"; setChoice: (c: ThemeChoice) => void };
const ThemeCtx = createContext<Ctx>({ choice: "system", resolved: "light", setChoice: () => undefined });
export const useTheme = () => useContext(ThemeCtx);

function systemDark() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function apply(dark: boolean) {
  const root = document.documentElement;
  // switch every color at once instead of letting each element's transition run
  root.classList.add("theme-switching");
  root.classList.toggle("dark", dark);
  window.setTimeout(() => root.classList.remove("theme-switching"), 0);
}

/** Light, dark or follow the OS, remembered per browser. No library: the class on <html> drives the tokens in
 *  globals.css and the dark: variants. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>("system");
  const [resolved, setResolved] = useState<"light" | "dark">("light");

  useEffect(() => {
    let saved: ThemeChoice = "system";
    try { saved = (localStorage.getItem(THEME_KEY) as ThemeChoice) || "system"; } catch { /* storage unavailable */ }
    setChoiceState(saved);
    setResolved(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  useEffect(() => {
    if (choice !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => { apply(media.matches); setResolved(media.matches ? "dark" : "light"); };
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [choice]);

  const setChoice = useCallback((next: ThemeChoice) => {
    try { localStorage.setItem(THEME_KEY, next); } catch { /* storage unavailable */ }
    const dark = next === "dark" || (next === "system" && systemDark());
    apply(dark);
    setChoiceState(next);
    setResolved(dark ? "dark" : "light");
  }, []);

  return <ThemeCtx.Provider value={{ choice, resolved, setChoice }}>{children}</ThemeCtx.Provider>;
}
