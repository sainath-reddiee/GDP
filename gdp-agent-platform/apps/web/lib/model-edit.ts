// Pure edits of a domain model specification (lib/model-types.ts) used by the Model Workbench. Every function returns
// a new specification; renames carry through business keys, unique constraints, cluster keys and relationships.

import type { DmAttribute, DmEntity, DmRelationship, DmSpec } from "./model-types";

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
export const upper = (s: string) => s.trim().toUpperCase().replace(/[^A-Z0-9_$]+/g, "_");

function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `id-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

export function blankAttribute(name = "NEW_COLUMN"): DmAttribute {
  return {
    id: uid(), name: upper(name), datatype: "VARCHAR", nullable: true, is_pk: false, description: "", business_definition: "",
    semantic_type: null, pii: { flag: false, classification: null }, accepted_values: [], default: null, is_unique: false, check: null,
    status: "ACTIVE", tags: [], business_key: false, source_columns: [], derived: true, transformation_hint: "", rationale: "",
  };
}

export function blankEntity(name: string, subjectArea: string | null = null): DmEntity {
  return {
    id: uid(), entity_name: upper(name), table_type: "TABLE", kind: "TABLE", scd_type: "1", grain: "", business_keys: [], description: "",
    purpose: "", subject_area: subjectArea, status: "ACTIVE", database: null, schema: null, model_spec: null, unique_constraints: [],
    checks: [], physical: { cluster_by: [], transient: false, retention_days: null },
    attributes: [{ ...blankAttribute("ID"), datatype: "NUMBER(38,0)", nullable: false, is_pk: true }],
  };
}

export function updateEntity(spec: DmSpec, id: string, patch: Partial<DmEntity>): DmSpec {
  const out = clone(spec);
  const e = out.design.entities.find((x) => x.id === id);
  if (!e) return out;
  if (patch.entity_name !== undefined && upper(patch.entity_name) !== e.entity_name) {
    const from = e.entity_name, to = upper(patch.entity_name);
    for (const r of out.relationships) {
      if (r.from_entity === from) r.from_entity = to;
      if (r.to_entity === from) r.to_entity = to;
    }
    if (out.design.primary_entity === from) out.design.primary_entity = to;
    patch = { ...patch, entity_name: to };
  }
  Object.assign(e, patch);
  if (patch.table_type) e.kind = ["DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE"].includes(patch.table_type) ? patch.table_type : "TABLE";
  if (patch.description !== undefined) e.purpose = patch.description.slice(0, 500);
  return out;
}

export function updateAttribute(spec: DmSpec, entityId: string, attrId: string, patch: Partial<DmAttribute>): DmSpec {
  const out = clone(spec);
  const e = out.design.entities.find((x) => x.id === entityId);
  const a = e?.attributes.find((x) => x.id === attrId);
  if (!e || !a) return out;
  if (patch.name !== undefined && upper(patch.name) !== a.name) {
    const from = a.name, to = upper(patch.name);
    e.business_keys = e.business_keys.map((k) => (k === from ? to : k));
    e.unique_constraints = e.unique_constraints.map((u) => u.map((c) => (c === from ? to : c)));
    e.physical.cluster_by = e.physical.cluster_by.map((c) => (c === from ? to : c));
    for (const r of out.relationships) {
      if (r.from_entity === e.entity_name && r.from_column === from) r.from_column = to;
      if (r.to_entity === e.entity_name && r.to_column === from) r.to_column = to;
    }
    patch = { ...patch, name: to };
  }
  if (patch.datatype !== undefined) patch = { ...patch, datatype: patch.datatype.toUpperCase() };
  Object.assign(a, patch);
  if (patch.is_pk) a.nullable = false;
  if (patch.business_key !== undefined) {
    e.business_keys = patch.business_key ? Array.from(new Set([...e.business_keys, a.name])) : e.business_keys.filter((k) => k !== a.name);
  }
  return out;
}

export function addEntity(spec: DmSpec, name: string, subjectArea: string | null = null): { spec: DmSpec; id: string } {
  const out = clone(spec);
  let n = upper(name) || "NEW_ENTITY";
  const taken = new Set(out.design.entities.map((e) => e.entity_name));
  for (let i = 2; taken.has(n); i++) n = `${upper(name) || "NEW_ENTITY"}_${i}`;
  const e = blankEntity(n, subjectArea);
  out.design.entities.push(e);
  return { spec: out, id: e.id };
}

export function addAttribute(spec: DmSpec, entityId: string): DmSpec {
  const out = clone(spec);
  const e = out.design.entities.find((x) => x.id === entityId);
  if (!e) return out;
  let n = "NEW_COLUMN";
  const taken = new Set(e.attributes.map((a) => a.name));
  for (let i = 2; taken.has(n); i++) n = `NEW_COLUMN_${i}`;
  e.attributes.push(blankAttribute(n));
  return out;
}

/** Remove a column that was never approved, or retire one the approved model has (kept so the review shows it). */
export function dropAttribute(spec: DmSpec, entityId: string, attrId: string, approvedIds: Set<string>): DmSpec {
  const out = clone(spec);
  const e = out.design.entities.find((x) => x.id === entityId);
  const a = e?.attributes.find((x) => x.id === attrId);
  if (!e || !a) return out;
  if (approvedIds.has(attrId)) {
    a.status = a.status === "RETIRED" ? "ACTIVE" : "RETIRED";
  } else {
    e.attributes = e.attributes.filter((x) => x.id !== attrId);
    e.business_keys = e.business_keys.filter((k) => k !== a.name);
    out.relationships = out.relationships.filter((r) => !((r.from_entity === e.entity_name && r.from_column === a.name)
      || (r.to_entity === e.entity_name && r.to_column === a.name)));
  }
  return out;
}

export function dropEntity(spec: DmSpec, entityId: string, approvedIds: Set<string>): DmSpec {
  const out = clone(spec);
  const e = out.design.entities.find((x) => x.id === entityId);
  if (!e) return out;
  if (approvedIds.has(entityId)) {
    e.status = e.status === "RETIRED" ? "ACTIVE" : "RETIRED";
  } else {
    out.design.entities = out.design.entities.filter((x) => x.id !== entityId);
    out.relationships = out.relationships.filter((r) => r.from_entity !== e.entity_name && r.to_entity !== e.entity_name);
  }
  return out;
}

export function moveAttribute(spec: DmSpec, entityId: string, attrId: string, delta: -1 | 1): DmSpec {
  const out = clone(spec);
  const e = out.design.entities.find((x) => x.id === entityId);
  if (!e) return out;
  const i = e.attributes.findIndex((a) => a.id === attrId), j = i + delta;
  if (i < 0 || j < 0 || j >= e.attributes.length) return out;
  [e.attributes[i], e.attributes[j]] = [e.attributes[j], e.attributes[i]];
  return out;
}

export function addRelationship(spec: DmSpec, rel: Omit<DmRelationship, "id" | "name"> & { name?: string }): DmSpec {
  const out = clone(spec);
  out.relationships.push({ ...rel, id: uid(), name: rel.name || `FK_${rel.from_entity}_${rel.from_column}`.slice(0, 255) });
  return out;
}

export function updateRelationship(spec: DmSpec, id: string, patch: Partial<DmRelationship>): DmSpec {
  const out = clone(spec);
  const r = out.relationships.find((x) => x.id === id);
  if (r) Object.assign(r, patch);
  return out;
}

export function dropRelationship(spec: DmSpec, id: string): DmSpec {
  const out = clone(spec);
  out.relationships = out.relationships.filter((r) => r.id !== id);
  return out;
}

export const sameSpec = (a: DmSpec | null, b: DmSpec | null) => JSON.stringify(a) === JSON.stringify(b);
