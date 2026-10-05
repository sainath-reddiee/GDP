const HIDDEN_TARGET_TABLES = new Set(["COMPLETE_EMPLOYEE_DETAILS"]);
const HIDDEN_TARGET_DATABASES = new Set(["ALATION_POC"]);
const HIDDEN_TARGET_SCHEMAS = new Set(["GDP_SILVER"]);
const HIDDEN_TARGET_IDS = new Set(["00000000-0000-4000-a000-000000000002"]);
const HIDDEN_DOMAIN_NAMES = new Set(["GDP"]);

function upper(value?: string | null) {
  return (value || "").trim().toUpperCase();
}

export function displayDomain(name?: string | null) {
  if (!name || HIDDEN_DOMAIN_NAMES.has(upper(name))) return null;
  return name.trim();
}

export function isHiddenTarget(row: {
  target_table?: string; target_database?: string; target_schema?: string; target_table_id?: string; fqn?: string;
}) {
  const table = upper(row.target_table);
  const database = upper(row.target_database) || upper(row.fqn?.split(".")[0]);
  const schema = upper(row.target_schema) || upper(row.fqn?.split(".")[1]);
  const ident = (row.target_table_id || "").trim();
  return HIDDEN_TARGET_IDS.has(ident)
    || HIDDEN_TARGET_TABLES.has(table)
    || HIDDEN_TARGET_DATABASES.has(database)
    || HIDDEN_TARGET_SCHEMAS.has(schema);
}

export function sourceSystemName(database?: string | null, schema?: string | null, explicit?: string | null) {
  const strip = (raw: string) => raw.replace(/_?GDP_?/gi, "_").replace(/_+/g, "_").replace(/^_|_$/g, "");
  const seed = explicit && !HIDDEN_DOMAIN_NAMES.has(upper(explicit)) ? strip(explicit) : strip(schema || database || "SOURCE");
  const ident = seed.replace(/[^A-Za-z0-9_]/g, "");
  if (!ident) return "SOURCE";
  return (ident[0].match(/\d/) ? `S_${ident}` : ident).slice(0, 64);
}

export function visibleDomains<T extends { domain_name: string }>(domains: T[]) {
  return domains.filter((d) => displayDomain(d.domain_name));
}

export function isModelTable(name?: string | null) {
  return /^(SILVER_|DIM_|FCT_|FACT_)/i.test(name || "");
}

function stem(name: string) {
  return name.replace(/^(SILVER_|DIM_|FCT_|FACT_|SRC_|RAW_|STG_)/i, "").replace(/S$/i, "").toUpperCase();
}

export function localModelSuggestions(
  database: string,
  schema: string,
  tables: { table_name: string }[],
  sourceTables: string[],
) {
  return tables.filter((t) => isModelTable(t.table_name)).map((t) => {
    const modelStem = stem(t.table_name);
    const matched = sourceTables.filter((src) => {
      const srcStem = stem(src);
      return srcStem === modelStem || srcStem.includes(modelStem) || modelStem.includes(srcStem);
    });
    return {
      kind: "existing" as const,
      target_table: t.table_name,
      fqn: `${database}.${schema}.${t.table_name}`,
      domain_name: null as string | null,
      score: matched.length ? 0.85 : 0.4,
      overlap_columns: [] as string[],
      reason: matched.length
        ? `Matches selected source ${matched.slice(0, 3).join(", ")}`
        : "Model table in this catalog",
    };
  });
}
