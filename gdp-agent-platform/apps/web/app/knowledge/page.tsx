import Link from "next/link";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { searchKnowledge } from "./actions";
import { KnowledgeSearch } from "./search-form";

export default async function Knowledge() {
  const { domains } = await api<{ domains: { domain_id: string; domain_name: string; knowledge_items: number }[] }>("/api/domains");
  const total = domains.reduce((n, d) => n + d.knowledge_items, 0);
  return (
    <div className="space-y-5">
      <h2>Knowledge</h2>
      <Card>
        <CardHeader>
          <CardTitle>{total} current knowledge items across {domains.length} domains</CardTitle>
          <CardDescription>
            Glossary, rules, patterns and approved mappings that ground mapping and codegen. Search uses Cortex Search
            over current, active items. See <Link href="/domains" className="text-primary hover:underline">Domains</Link>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <KnowledgeSearch action={searchKnowledge} />
        </CardContent>
      </Card>
    </div>
  );
}
