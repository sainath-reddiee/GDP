"use client";

import { useMemo, useState } from "react";
import type { SourceOverview } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { StageAction } from "@/components/stage-action";
import { validateAccess } from "../source-actions";

const MAX_SELECTED = 50;

export function ObjectSelector({ runId, objects, planned = [] }: {
  runId: string; objects: SourceOverview["objects"]; planned?: string[];
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => {
    const fromRun = objects.filter((o) => o.selected_flag).map((o) => o.object_name);
    const seed = fromRun.length ? fromRun : planned.filter((name) => objects.some((o) => o.object_name === name));
    return new Set(seed);
  });
  const visible = useMemo(
    () => objects.filter((o) => o.object_name.toLowerCase().includes(query.toLowerCase())),
    [objects, query],
  );
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
          {objects.length} objects in this schema. Search, then select the tables the platform should read and land.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables or views" className="mb-3 max-w-sm" />
        <Table>
          <THead><TR><TH className="w-10"></TH><TH>Object</TH><TH>Type</TH><TH>Rows (estimate)</TH><TH>Last altered</TH></TR></THead>
          <TBody>
            {visible.length === 0 && (
              <TR><TD colSpan={5} className="py-6 text-center text-muted-foreground">No objects match that search.</TD></TR>
            )}
            {visible.map((o) => (
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
