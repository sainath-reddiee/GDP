"use client";

import type { ReactNode } from "react";
import { configureCatalogDisplay, type CatalogDisplayConfig } from "@/lib/catalog-display";

/** Applies the API's catalog display lists in the browser before any page below it renders. */
export function CatalogDisplayProvider({ config, children }: { config: CatalogDisplayConfig | null; children: ReactNode }) {
  configureCatalogDisplay(config);
  return <>{children}</>;
}
