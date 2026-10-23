import { PERSONA_REQUIRES, hasAny, type Grants } from "./access";

// The personas of the Agentic Data OS experience. A person is offered only the personas their governance roles
// qualify them for (lib/os/access PERSONA_REQUIRES); the chosen one changes what /os puts first, never what they may do.

export const PERSONA_COOKIE = "ados_persona";

export type PersonaId = "forward_lead" | "product_owner" | "admin" | "designer" | "developer" | "qa" | "reliability" | "curator" | "governance" | "viewer";

export type Persona = {
  id: PersonaId; label: string; short: string; responsibility: string;
  /** the workflow states this persona acts on, first in the queue */
  states: string[];
  /** the /os page this persona starts from */
  home: string;
};

export const PERSONAS: Persona[] = [
  { id: "forward_lead", label: "Forward Engineering Lead", short: "Forward Lead", home: "/os/build",
    responsibility: "Orchestrate generation, independent validation and skill improvement",
    states: ["DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_FAILED", "VALIDATION_PASSED", "FAILED"] },
  { id: "product_owner", label: "GDP Product Owner", short: "Product Owner", home: "/os/intake",
    responsibility: "Describe intent, link sources and domains, sign off UAT",
    states: ["CREATED"] },
  { id: "admin", label: "Data OS Administrator", short: "Administrator", home: "/os/enable",
    responsibility: "Source access, profiling handles and ingestion configuration",
    states: ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"] },
  { id: "designer", label: "Data Designer", short: "Designer", home: "/os/design",
    responsibility: "Profiling, domain model, source mapping and DQ contract",
    states: ["LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING",
             "MAPPING_REVIEW", "MAPPING_APPROVED", "STTM_PENDING", "STTM_REVIEW", "STTM_APPROVED"] },
  { id: "developer", label: "Data Developer", short: "Developer", home: "/os/release",
    responsibility: "Harden code, review the PR, release within the SDLC",
    states: ["DBT_REVIEW", "DBT_APPROVED", "DEPLOYMENT_PENDING"] },
  { id: "qa", label: "QA & UAT Lead", short: "QA & UAT", home: "/os/release",
    responsibility: "Regression, functional acceptance and release evidence",
    states: ["SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_REVIEW"] },
  { id: "reliability", label: "Data Reliability Engineer", short: "Reliability", home: "/os/run",
    responsibility: "Runtime observability, DQ checks, incidents and RCA",
    states: ["COMPLETED", "DEPLOYED", "PIPELINE_ENABLED", "FAILED"] },
  { id: "curator", label: "Knowledge & Skills Curator", short: "Curator", home: "/os/knowledge",
    responsibility: "Verify Enterprise Memory and release reviewed skill versions",
    states: [] },
  { id: "governance", label: "Governance Lead", short: "Governance", home: "/os/review",
    responsibility: "Roles, approval policies, change requests and the audit trail",
    states: ["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW", "DBT_REVIEW"] },
  { id: "viewer", label: "Viewer", short: "Viewer", home: "/os",
    responsibility: "Read the platform; no changes, no AI calls",
    states: [] },
];

export const DEFAULT_PERSONA: PersonaId = "forward_lead";
export const personaById = (id: string | null | undefined) => PERSONAS.find((p) => p.id === id) ?? PERSONAS[0];

/** The personas a person's privileges qualify them for, in list order; Viewer when none does. */
export function personasFor(g: Grants): Persona[] {
  const fit = PERSONAS.filter((p) => p.id !== "viewer" && hasAny(g, PERSONA_REQUIRES[p.id]));
  return fit.length ? fit : PERSONAS.filter((p) => p.id === "viewer");
}
