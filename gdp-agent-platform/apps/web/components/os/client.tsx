"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import { ErCanvas } from "@/components/hub/er-canvas";
import type { ModelEntity, ModelRelationship } from "@/lib/hub-types";
import { cn } from "@/lib/utils";

// The few interactive pieces of the /os screens.

/** A GET form that re-renders the page as soon as a field changes (filters, pickers). */
export function AutoForm({ action, children, className }: { action: string; children: ReactNode; className?: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  return (
    <form action={action} className={cn(className, pending && "opacity-70 transition-opacity")}
          onChange={(e) => {
            const form = e.currentTarget;
            const qs = new URLSearchParams();
            new FormData(form).forEach((v, k) => { if (typeof v === "string" && v) qs.append(k, v); });
            start(() => router.replace(`${action}?${qs}`, { scroll: false }));
          }}>
      {children}
    </form>
  );
}

/** The domain's data model as the shared ER canvas, with entity selection. */
export function DomainEr({ entities, relationships, className }: {
  entities: ModelEntity[]; relationships: (ModelRelationship & { from_optional?: boolean })[]; className?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  return <ErCanvas className={className} platform="SNOWFLAKE" entities={entities} relationships={relationships} selected={selected} onSelect={setSelected} />;
}
