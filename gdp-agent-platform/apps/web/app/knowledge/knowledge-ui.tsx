import {
  BadgeCheck, BookOpen, Bot, Boxes, Code2, FileText, GitCompare, Hand, Layers, ListChecks, MessageSquare, Package, Ruler,
  ScanSearch, ShieldCheck, Sparkles, TableProperties, TriangleAlert, Wand2, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

export const prettyType = (t: string) => t.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())
  .replace(/\bdbt\b/i, "dbt").replace(/\bsttm\b/i, "STTM").replace(/\bqa\b/i, "QA").replace(/\bsoda\b/i, "Soda");

const TYPE_ICON: Record<string, LucideIcon> = {
  GLOSSARY: BookOpen, BUSINESS_RULE: Ruler, TRANSFORMATION_RULE: Wand2, MAPPING_PATTERN: GitCompare, MODEL_DEFINITION: Boxes,
  NAMING_STANDARD: TableProperties, DBT_PATTERN: Code2, SODA_PATTERN: ShieldCheck, EXCEPTION: TriangleAlert,
  STTM_TEMPLATE: Layers, ONBOARDING_GUIDE: FileText, COLUMN_RULE: ScanSearch, QA_TEST: ListChecks,
};

export function TypeIcon({ type, className }: { type: string; className?: string }) {
  const Icon = TYPE_ICON[type] ?? FileText;
  return <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary", className)} title={prettyType(type)}><Icon className="h-3.5 w-3.5" /></span>;
}

export const ORIGINS: Record<string, { label: string; icon: LucideIcon; tone: string }> = {
  USER: { label: "Added by a person", icon: Hand, tone: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300" },
  SEED: { label: "Repository pack", icon: Package, tone: "bg-muted text-muted-foreground" },
  PACK_IMPORT: { label: "Imported pack", icon: Package, tone: "bg-muted text-muted-foreground" },
  COPILOT: { label: "Copilot", icon: MessageSquare, tone: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300" },
  MAPPING: { label: "Learned in mapping", icon: GitCompare, tone: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300" },
  STTM: { label: "Learned in STTM", icon: Layers, tone: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" },
  SODA: { label: "Learned in data quality", icon: ShieldCheck, tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" },
  PROFILING: { label: "Learned in profiling", icon: ScanSearch, tone: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300" },
  MODELING: { label: "Learned in modeling", icon: Boxes, tone: "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300" },
  QA: { label: "Proven QA test", icon: ListChecks, tone: "bg-cyan-100 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300" },
  QUALITY: { label: "Quality scan", icon: TriangleAlert, tone: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" },
  DBT: { label: "Learned in dbt", icon: Code2, tone: "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300" },
  DOMAIN_AI: { label: "AI domain review", icon: Bot, tone: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300" },
  SUGGESTION: { label: "AI suggestion", icon: Sparkles, tone: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300" },
};

export function OriginChip({ origin, short }: { origin?: string | null; short?: boolean }) {
  const o = ORIGINS[(origin || "USER").toUpperCase()] ?? { label: origin ?? "Unknown", icon: Sparkles, tone: "bg-muted text-muted-foreground" };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium", o.tone)} title={o.label}>
      <o.icon className="h-3 w-3" />{short ? (origin ?? "").replace(/_/g, " ").toLowerCase() : o.label}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  PROPOSED: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  SUPERSEDED: "bg-muted text-muted-foreground border-border",
  REJECTED: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  RETIRED: "bg-muted text-muted-foreground border-border",
  DRAFT: "bg-muted text-muted-foreground border-border",
};

export function StatusPill({ status }: { status: string }) {
  return <span className={cn("rounded-full border px-1.5 py-0.5 text-[10px] font-medium lowercase", STATUS_TONE[status] ?? STATUS_TONE.DRAFT)}>{status}</span>;
}

export function Verified({ by, at }: { by?: string | null; at?: string | null }) {
  if (!at) return null;
  return <span title={`Verified by ${by ?? "someone"} on ${at.slice(0, 10)}`} className="inline-flex items-center text-emerald-600"><BadgeCheck className="h-3.5 w-3.5" /></span>;
}
