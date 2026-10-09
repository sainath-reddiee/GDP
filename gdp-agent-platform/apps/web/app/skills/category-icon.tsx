import {
  BadgeCheck, Boxes, CodeXml, Folder, GitCompare, Landmark, Plug, ScanSearch, ShieldCheck, Sparkles, TableProperties,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  plug: Plug, "scan-search": ScanSearch, boxes: Boxes, "git-compare": GitCompare, "table-properties": TableProperties,
  "shield-check": ShieldCheck, "code-2": CodeXml, "badge-check": BadgeCheck, landmark: Landmark, sparkles: Sparkles,
  folder: Folder,
};
export const ICON_NAMES = Object.keys(ICONS);
export const CATEGORY_COLORS = ["#0ea5e9", "#6366f1", "#8b5cf6", "#14b8a6", "#f59e0b", "#22c55e", "#ef4444", "#0891b2", "#a855f7", "#64748b"];

/** A category's icon on a soft tile of its colour. */
export function CategoryIcon({ icon, color, size = "md", className }: {
  icon?: string | null; color?: string | null; size?: "sm" | "md" | "lg"; className?: string;
}) {
  const Icon = ICONS[icon ?? ""] ?? Folder;
  const tint = color ?? "#64748b";
  return (
    <span className={cn("inline-grid shrink-0 place-items-center rounded-lg",
                        size === "sm" ? "h-6 w-6" : size === "lg" ? "h-10 w-10 rounded-xl" : "h-8 w-8", className)}
          style={{ backgroundColor: `${tint}1f`, color: tint }}>
      <Icon className={size === "sm" ? "h-3.5 w-3.5" : size === "lg" ? "h-5 w-5" : "h-4 w-4"} />
    </span>
  );
}
