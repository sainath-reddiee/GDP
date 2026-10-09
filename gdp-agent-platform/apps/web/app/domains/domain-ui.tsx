const COLORS = ["#6366f1", "#14b8a6", "#f59e0b", "#ef4444", "#8b5cf6", "#0ea5e9", "#22c55e", "#ec4899"];
export const colorFor = (s: string) => COLORS[[...s].reduce((n, c) => n + c.charCodeAt(0), 0) % COLORS.length];
const initials = (u: string) => u.split(/[._\s@]+/).filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase() || "?";

export function cleanDescription(text: string | null | undefined) {
  return (text || "").replace(/Global Data Platform\s*/i, "").replace(/\bGDP\b/g, "").trim() || null;
}

export function Avatar({ user, role, size = "sm" }: { user: string; role?: string; size?: "sm" | "md" }) {
  return (
    <span title={role ? `${user} (${role.toLowerCase()})` : user}
          className={`grid shrink-0 place-items-center rounded-full font-semibold text-white ring-2 ring-card ${size === "md" ? "h-8 w-8 text-[11px]" : "h-6 w-6 text-[9px]"}`}
          style={{ backgroundColor: colorFor(user) }}>{initials(user)}</span>
  );
}

export function Avatars({ members }: { members: { user: string; role: string }[] }) {
  const sorted = [...members.filter((m) => m.role === "OWNER"), ...members.filter((m) => m.role !== "OWNER")];
  return (
    <span className="flex -space-x-1.5">
      {sorted.slice(0, 4).map((m) => <Avatar key={`${m.user}-${m.role}`} user={m.user} role={m.role} />)}
      {members.length > 4 && <span className="grid h-6 w-6 place-items-center rounded-full bg-muted text-[9px] ring-2 ring-card">+{members.length - 4}</span>}
    </span>
  );
}

export function DomainMark({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  return (
    <span className={`grid shrink-0 place-items-center font-bold text-white ${size === "lg" ? "h-12 w-12 rounded-2xl text-base" : "h-10 w-10 rounded-xl text-sm"}`}
          style={{ backgroundColor: colorFor(name) }}>{name.slice(0, 2).toUpperCase()}</span>
  );
}
