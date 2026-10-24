"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { FileSearch, Plus, Settings2 } from "lucide-react";
import { useAccess } from "@/components/access";
import { PolicyDialog, useOpenItem } from "@/app/knowledge/knowledge-hub";
import { ItemEditor } from "@/app/knowledge/knowledge-browser";

/** Opens the full record (content, versions with diff and rollback, provenance, usage track record, edit, retire). */
export function OpenRecord({ id, label = "Full record" }: { id: string; label?: string }) {
  const { show, drawer } = useOpenItem();
  return (
    <>
      <button type="button" className="os-btn" onClick={() => show(id)}><FileSearch className="h-3.5 w-3.5" />{label}</button>
      {drawer}
    </>
  );
}

/** Steward tools for the hub header: add a record and set the learning policy. */
export function KnowledgeTools({ domains }: { domains: { domain_id: string; domain_name: string }[] }) {
  const router = useRouter();
  const { canAct } = useAccess();
  const [adding, setAdding] = useState(false);
  const [policy, setPolicy] = useState(false);
  if (!canAct("KNOWLEDGE.EDIT")) return null;
  return (
    <>
      <button type="button" className="os-btn" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" />Add knowledge</button>
      <button type="button" className="os-btn" onClick={() => setPolicy(true)}><Settings2 className="h-3.5 w-3.5" />Learning policy</button>
      {adding && <ItemEditor domains={domains} item={null} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); router.refresh(); }} />}
      {policy && <PolicyDialog onClose={() => setPolicy(false)} />}
    </>
  );
}
