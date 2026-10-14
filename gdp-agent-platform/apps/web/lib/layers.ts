/** The layers of a domain, the same everywhere: Silver (pre-MDM), MDM (Reltio, for the domains it masters) and Gold.
 *  Silver and Gold are also the two pipelines (Silver Pipeline, Gold Pipeline); MDM is Reltio's, read only. */
export type Layer = "SILVER" | "MDM" | "GOLD";
export type MdmRole = "MASTERED" | "CONSUMER" | null | undefined;

export const LAYER_META: Record<Layer, { label: string; sub: string; chip: string; solid: string; ring: string }> = {
  SILVER: { label: "Silver", sub: "pre-MDM, one row per source record", chip: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
            solid: "bg-slate-600 text-white", ring: "border-slate-400/60" },
  MDM: { label: "MDM (Reltio)", sub: "mastered: cross-reference and golden records", chip: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
         solid: "bg-violet-600 text-white", ring: "border-violet-500/50" },
  GOLD: { label: "Gold", sub: "star schema: facts, dimensions, bridges", chip: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
          solid: "bg-amber-500 text-white", ring: "border-amber-500/50" },
};

/** The layers a domain has: a domain Reltio masters has an MDM layer between Silver and Gold. */
export const layersFor = (role: MdmRole): Layer[] => (String(role ?? "").toUpperCase() === "MASTERED" ? ["SILVER", "MDM", "GOLD"] : ["SILVER", "GOLD"]);

export const layerOf = (value: string | null | undefined, role: MdmRole): Layer => {
  const v = String(value ?? "").toUpperCase();
  return (layersFor(role) as string[]).includes(v) ? (v as Layer) : "SILVER";
};

/** How a domain sits in MDM, in a few words. */
export function mdmBadge(role: MdmRole, consumes?: string | null): { text: string; cls: string } | null {
  const r = String(role ?? "").toUpperCase();
  if (r === "MASTERED") return { text: "Mastered by Reltio", cls: LAYER_META.MDM.chip };
  if (r === "CONSUMER") return { text: `Consumes ${String(consumes ?? "").split(",").filter(Boolean).map((d) => d.charAt(0) + d.slice(1).toLowerCase()).join(" and ") || "mastered domains"}`,
                                 cls: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300" };
  return null;
}
