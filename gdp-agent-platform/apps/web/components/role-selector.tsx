"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { Check, ChevronsUpDown, Loader2, Search } from "lucide-react";
import { loadSnowflakeRoles, setSnowflakeRole } from "@/app/role-actions";
import { cn } from "@/lib/utils";

/** The Snowflake role the app uses for this person, picked from the roles their user has, with a search box: an
 *  account can grant dozens of roles. Type to filter, arrow keys to move, Enter to pick, Esc to close. */
export function RoleSelector({ currentRole }: { currentRole: string | null }) {
  const [roles, setRoles] = useState<string[]>(currentRole ? [currentRole] : []);
  const [value, setValue] = useState(currentRole ?? "");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    loadSnowflakeRoles().then((result) => {
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setRoles(result.data.roles);
      setValue(result.data.current);
    });
  }, []);

  // close on a click outside
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const shown = useMemo(() => {
    const q = query.trim().toUpperCase();
    return q ? roles.filter((r) => r.toUpperCase().includes(q)) : roles;
  }, [roles, query]);

  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const toggle = () => {
    if (pending || roles.length === 0) return;
    setOpen((o) => !o);
    setQuery("");
    setTimeout(() => input.current?.focus(), 0);
  };

  const pick = (next: string) => {
    setOpen(false);
    if (next === value) return;
    const previous = value;
    setValue(next);
    setError("");
    start(async () => {
      const result = await setSnowflakeRole(next);
      if (!result.ok) { setError(result.error); setValue(previous); }  // show the role that is really in use
    });
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) pick(shown[active]); }
    else if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
  };

  return (
    <div ref={box} className="relative">
      <label htmlFor="snowflake_role" className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Snowflake role
      </label>
      <button
        id="snowflake_role"
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={pending || roles.length === 0}
        onClick={toggle}
        className="flex w-full items-center gap-2 rounded-lg border border-input bg-card px-2 py-1.5 text-left text-xs text-foreground outline-none transition hover:border-primary/40 focus:border-primary/60 focus:ring-4 focus:ring-primary/10 disabled:opacity-60"
      >
        <span className="min-w-0 flex-1 truncate font-mono">{value || "No role"}</span>
        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : <ChevronsUpDown className="h-3.5 w-3.5 text-muted-foreground" />}
      </button>

      {open && (
        <div className="absolute inset-x-0 z-50 mt-1 overflow-hidden rounded-lg border bg-card text-card-foreground shadow-lg">
          <div className="flex items-center gap-1.5 border-b px-2 py-1.5">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <input
              ref={input}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKey}
              placeholder={`Search ${roles.length} roles`}
              aria-label="Search roles"
              aria-controls="snowflake_role_list"
              aria-activedescendant={shown[active] ? `role-${active}` : undefined}
              className="w-full bg-transparent text-xs outline-none placeholder:text-muted-foreground"
            />
          </div>
          <ul ref={list} id="snowflake_role_list" role="listbox" aria-label="Snowflake roles" className="max-h-60 overflow-y-auto py-1">
            {shown.length === 0 && <li className="px-2.5 py-2 text-xs text-muted-foreground">No role matches “{query.trim()}”.</li>}
            {shown.map((role, i) => (
              <li
                key={role}
                id={`role-${i}`}
                data-index={i}
                role="option"
                aria-selected={role === value}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => { e.preventDefault(); pick(role); }}
                className={cn("flex cursor-pointer items-center gap-2 px-2.5 py-1.5 font-mono text-xs",
                  i === active ? "bg-muted" : "", role === value && "font-semibold text-primary")}
              >
                <span className="min-w-0 flex-1 truncate" title={role}>{role}</span>
                {role === value && <Check className="h-3.5 w-3.5 shrink-0" />}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-1 text-[10px] leading-snug text-muted-foreground">
        Catalog browse and source register use this role. Pick one that can see your bronze/silver objects.
      </p>
      {error && <p className="mt-1 text-[10px] text-destructive">{error}</p>}
    </div>
  );
}
