"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { moveToSprint, sprintChoices, type SprintChoice } from "./actions";

/** One Jira ticket's sprint: pick an active or future sprint of its project's boards, or the backlog. */
export function SprintControl({ issueKey }: { issueKey: string }) {
  const project = issueKey.split("-")[0];
  const [sprints, setSprints] = useState<SprintChoice[] | null>(null);
  const [usable, setUsable] = useState(true);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void sprintChoices(project).then((r) => {
      if (!live) return;
      if (!r.ok) { setSprints([]); setUsable(false); setNote(r.error); return; }
      setSprints(r.data.sprints);
      setUsable(!!r.data.sprint_field);
    });
    return () => { live = false; };
  }, [project]);

  const apply = async () => {
    if (!value) return;
    setBusy(true); setNote(null);
    const r = await moveToSprint([issueKey], value === "backlog" ? null : Number(value)).catch(() => null);
    setBusy(false);
    if (!r?.ok) { setNote(r ? r.error : "The ticket could not be moved."); return; }
    setNote(r.data.failed.length ? r.data.failed[0].error : `Moved to ${value === "backlog" ? "the backlog" : sprints?.find((s) => String(s.id) === value)?.name}.`);
  };

  if (sprints && !usable && !note) return null;   // no Jira Software on this site
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="sprint-control">
      <select aria-label={`Sprint for ${issueKey}`} value={value} onChange={(e) => setValue(e.target.value)} disabled={!sprints?.length && !usable}
              className="h-8 rounded-lg border border-border bg-background px-2 text-xs">
        <option value="">{sprints === null ? "Loading sprints…" : "Move to sprint…"}</option>
        {sprints?.map((s) => <option key={s.id} value={String(s.id)}>{s.name} ({s.state})</option>)}
        {usable && <option value="backlog">Backlog (no sprint)</option>}
      </select>
      <button type="button" onClick={() => void apply()} disabled={!value || busy}
              className="inline-flex h-8 items-center gap-1 rounded-lg border px-3 font-medium hover:bg-muted disabled:opacity-50">
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Move
      </button>
      {note && <span role="status" className="text-muted-foreground">{note}</span>}
    </div>
  );
}
