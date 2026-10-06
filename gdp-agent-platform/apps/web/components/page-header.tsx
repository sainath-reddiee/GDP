import type { ReactNode } from "react";

/** Consistent page title block: eyebrow, title, one-line description and right-aligned actions. */
export function PageHeader({ eyebrow, title, description, actions, children }: {
  eyebrow?: string; title: ReactNode; description?: ReactNode; actions?: ReactNode; children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="min-w-0 flex-1">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="mt-1">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}
        {children}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
