"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";

export function SttmFilters({ initial }: { initial: { q: string; source: string; target: string; status: string } }) {
  const router = useRouter();
  const path = usePathname();
  const [f, setF] = useState(initial);
  const apply = (next = f) => {
    const qs = new URLSearchParams(Object.entries(next).filter(([, v]) => v));
    router.push(`${path}${qs.toString() ? `?${qs}` : ""}`);
  };
  return (
    <form className="surface flex flex-wrap items-center gap-2 p-3" onSubmit={(e) => { e.preventDefault(); apply(); }}>
      <div className="relative min-w-[12rem] flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input aria-label="Search" className="pl-9" placeholder="Search entities" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
      </div>
      <Input aria-label="Source entity" className="w-44" placeholder="Source system or table" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} />
      <Input aria-label="Target entity" className="w-44" placeholder="Target table" value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} />
      <Select aria-label="Status" className="w-36" value={f.status} onChange={(e) => { const next = { ...f, status: e.target.value }; setF(next); apply(next); }}>
        <option value="">Any status</option>
        {["DRAFT", "REVIEW", "APPROVED", "SUPERSEDED"].map((s) => <option key={s} value={s}>{s.toLowerCase()}</option>)}
      </Select>
      <Button type="submit" variant="outline">Apply</Button>
    </form>
  );
}
