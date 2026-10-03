"use client";

import { useState } from "react";
import type { SourceOverview } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { StageAction } from "@/components/stage-action";
import { validateAccess } from "../source-actions";

const MAX_SELECTED = 50;

export function ObjectSelector({ runId, objects }: { runId: string; objects: SourceOverview["objects"] }) {
  const [selected, setSelected] = useState<Set<string>>(new Set(objects.filter((o) => o.selected_flag).map((o) => o.object_name)));
  const toggle = (name: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Select objects to onboard</CardTitle>
        <CardDescription>
          Validation checks the platform can read each selected object. Passing moves the run to landing.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <THead><TR><TH className="w-10"></TH><TH>Object</TH><TH>Type</TH><TH>Rows (estimate)</TH><TH>Last altered</TH></TR></THead>
          <TBody>
            {objects.map((o) => (
              <TR key={o.object_name}>
                <TD>
                  <input type="checkbox" aria-label={`select ${o.object_name}`} checked={selected.has(o.object_name)}
                         onChange={() => toggle(o.object_name)} className="h-4 w-4" />
                </TD>
                <TD className="font-medium">{o.object_name}</TD>
                <TD>{o.object_type}</TD>
                <TD>{o.row_count_estimate ?? "-"}</TD>
                <TD className="text-muted-foreground">{o.last_altered?.slice(0, 19) ?? "-"}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        <div className="mt-4 flex items-center gap-3">
          <StageAction
            label={`Validate access (${selected.size} selected)`}
            pendingLabel="Checking access…"
            disabled={selected.size === 0 || selected.size > MAX_SELECTED}
            action={() => validateAccess(runId, Array.from(selected))}
          />
        </div>
      </CardContent>
    </Card>
  );
}
