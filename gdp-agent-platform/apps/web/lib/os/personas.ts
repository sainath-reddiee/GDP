import { PERSONA_REQUIRES, hasAny, type Grants } from "./access";

// The personas of the Agentic Data OS experience; their ids are CORE.PERSONA's (V076). A super admin gives people
// personas in Admin, People, and each grants a bundle of governance roles. A person is offered the personas they were
// given (lib/os/session); PERSONA_REQUIRES only places people from before personas. The chosen one changes what /os
// puts first, never what they may do.

export const PERSONA_COOKIE = "ados_persona";

export type PersonaId = "forward_lead" | "product_owner" | "admin" | "designer" | "developer" | "qa" | "reliability" | "curator" | "super" | "viewer";

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
    // from the approved transformations on: Build end to end (checks, code, validation, pull request)
    states: ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING",
             "VALIDATION_RUNNING", "VALIDATION_FAILED", "VALIDATION_PASSED", "FAILED"] },
  { id: "product_owner", label: "GDP Product Owner", short: "Product Owner", home: "/os/intake",
    responsibility: "Describe intent, link sources and domains, sign off UAT",
    states: ["CREATED"] },
  { id: "admin", label: "Data OS Administrator", short: "Administrator", home: "/os/enable",
    responsibility: "Domain and source onboarding: access, landing and ingestion configuration",
    states: ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"] },
  { id: "designer", label: "Data Designer", short: "Designer", home: "/os/design",
    responsibility: "Profiling, domain model, source mapping and transformations (STTM)",
    states: ["LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING",
             "MAPPING_REVIEW", "MAPPING_APPROVED", "STTM_PENDING", "STTM_REVIEW"] },
  { id: "developer", label: "Data Developer", short: "Developer", home: "/os/release",
    responsibility: "Harden code, review the PR, release within the SDLC",
    states: ["DBT_REVIEW", "DBT_APPROVED", "DEPLOYMENT_PENDING"] },
  { id: "qa", label: "QA & UAT Lead", short: "QA & UAT", home: "/os/release",
    responsibility: "Regression, functional acceptance and release evidence",
    states: ["DBT_REVIEW"] },
  { id: "reliability", label: "Data Reliability Engineer", short: "Reliability", home: "/os/run",
    responsibility: "Runtime observability, DQ checks, incidents and RCA",
    states: ["COMPLETED", "DEPLOYED", "PIPELINE_ENABLED", "FAILED"] },
  { id: "curator", label: "Knowledge & Skills Curator", short: "Curator", home: "/os/knowledge",
    responsibility: "Verify Enterprise Memory and release reviewed skill versions",
    states: [] },
  { id: "super", label: "Platform Admin", short: "Platform Admin", home: "/os/people",
    responsibility: "Everything, and the only one who sees Admin and governance: people, personas, settings, audit",
    states: ["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW", "DBT_REVIEW", "VALIDATION_FAILED", "FAILED"] },
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

/** The persona whose work a privilege is, as the persona bundles hand them out (V076/V077): what Home and the request
 *  screens name as "waiting on", instead of a privilege or a governance role. */
export const PERSONA_FOR_PRIVILEGE: Record<string, PersonaId> = {
  "SOURCE.CONNECT": "admin", "DOMAIN.ONBOARD": "admin",
  "PROFILE.RUN": "designer", "MAPPING.DECIDE": "designer", "MAPPING.APPROVE": "designer", "STTM.EDIT": "designer",
  "STTM.APPROVE": "designer", "MODEL.EDIT": "designer", "MODEL.APPROVE": "designer", "SODA.APPROVE": "designer",
  "SODA.EDIT": "designer", "RUN.CREATE": "designer",
  "RUN.OPERATE": "forward_lead", "DBT.EDIT": "forward_lead", "SKILL.RELEASE": "forward_lead",
  "REVIEW.APPROVE": "developer", "REVIEW.DECIDE": "developer",
  "QA.SIGNOFF": "qa", "QA.EDIT": "qa", "PACKAGE.APPROVE": "qa", "PACKAGE.EDIT": "qa",
  "DOMAIN.EDIT": "product_owner", "REQUEST.CHANGES": "product_owner",
  "KNOWLEDGE.EDIT": "curator", "SKILL.EDIT": "curator",
  "OPS.OPERATE": "reliability", "OPS.FIX_APPROVE": "reliability", "OPS.FIX_PUBLISH": "reliability", "CASE.WORK": "reliability",
};
/** The persona whose bundle holds a governance role (who decides a change request routed to that role). */
export const PERSONA_FOR_ROLE: Record<string, PersonaId> = {
  PLATFORM_ADMIN: "admin", DATA_DESIGNER: "designer", MODEL_APPROVER: "designer", STTM_APPROVER: "designer",
  DQ_APPROVER: "designer", DATA_ENGINEER: "forward_lead", SKILL_OWNER: "forward_lead", CODE_REVIEWER: "developer",
  QA_LEAD: "qa", DATA_STEWARD: "product_owner", SUPPORT_ENGINEER: "reliability", SUPER_ADMIN: "super",
  GOVERNANCE_ADMIN: "super",
};
export const personaLabel = (id: PersonaId | null | undefined) => (id ? personaById(id).label : "a person");
/** The persona label for a privilege ("Data Designer" for STTM.APPROVE); the privilege itself when none is known. */
export const personaForPrivilege = (privilege: string) => {
  const id = PERSONA_FOR_PRIVILEGE[privilege];
  return id ? personaById(id).label : privilege;
};
