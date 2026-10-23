# Agentic Data OS experience (`/os`)

A persona-led front end for the platform, built from the CBRE ADOS UX package (ten screens, one shell, one eight-step lifecycle). Every signed-in page, the `/os` screens and all the classic pages alike, opens in the same shell. The rail has the package's ten entries, plus Admin for admins. Every page is a tab under one of them. No classic feature was rebuilt or removed: the classic pages render unchanged inside the new frame.

Every screen reads live platform data through the endpoints the classic pages already use. There is no sample data. When an endpoint fails or has nothing to return, the screen shows the reason or an empty state.

## The rail: ten entries, every page a tab

| Entry | Tabs |
|---|---|
| My Work | My Work (`/os`), Inbox (`/work`), Overview, Copilot, Value |
| Intake | New request, Requests, Onboarding wizard |
| Enable | Source readiness |
| Design | Designer studio, Domains, Stores (the six stores, with their own store tabs) |
| Build | Workbench (and every `/runs/[id]` workspace), Silver pipeline, Gold pipeline, Lineage, Code |
| Review | Gates, Approvals, Audit |
| Skills | Skill studio, Registry (including proposed skills) |
| Release | Release, Source control, QA testing |
| Knowledge | Knowledge hub, Enterprise Memory |
| Run & DQ | Run & DQ, Quality center, Monitoring, Incidents, Resolutions, Known fixes, Permanent fixes, Tickets (and Jira) |
| Admin | Admin (admins only) |

Each person sees only the entries and tabs their privileges open; an entry opens its first open tab. Every one of the 79 page routes belongs to exactly one tab (checked against the access list). In the new shell, the tab bar replaces the classic workspace tabs, so a page never shows two tab bars.

The header carries what the classic top bar did: the domain scope, global search (Ctrl K), the "New" menu, and the account menu (work-role switch, appearance, sign out). It also has "View as" and, for admins, "Preview as role".

**How it is wired**
- `middleware.ts` passes each signed-in request's path to the root layout (header `x-ados-path`, set by the server, never taken from the client).
- The root layout looks the path up in the access list. A page outside the person's role (or the previewed role) goes back to My Work with the reason, before it renders.
- `components/os/chrome.tsx` draws the frame. The classic page components themselves are unchanged.

**Classic layout (admins only).** Admin has a "Classic layout" switch (cookie `ados_classic`). The root layout honours it only for holders of `ADMIN.VIEW` or super admins. With it on, the classic pages get the old sidebar and top bar; `/os` keeps its shell either way. The e2e checks of the classic layout (`command-center.spec.ts`) set this cookie.

**Landing.** With `ADOS_LANDING=1`, signing in and opening `/` land on `/os` (mission control). The default is off, so people land on the classic My Work (`/work`), which now also opens in the new shell.

## Onboarding requests (V075)

A data owner files a request on Intake: a source, a new domain, or a data product, for a domain. The request then travels to whoever acts next. Nobody types its stage in. Every read works it out again from the domain, each source's registration and health, and the runs that onboard them (`services/onboarding/requests.py`).

| Stage | When | Who acts (privilege) |
|---|---|---|
| Domain | the domain does not exist yet, or was deleted or retired | Data steward (`DOMAIN.EDIT`) |
| Enable | a source is not registered, is unreachable, or is not landed | Data OS Administrator (`SOURCE.CONNECT`, `PROFILE.RUN`) |
| Design | no approved domain model, no run yet, or a run being profiled and mapped | Data Designer (`RUN.CREATE`, `RUN.OPERATE`) and the gate approvers (`MAPPING.APPROVE`, `STTM.APPROVE`, `SODA.APPROVE`) |
| Build | code generation and validation | Forward Engineering Lead (`RUN.OPERATE`) |
| Review | code review | Data Developer (`REVIEW.APPROVE`) |
| Done | every source's run is approved or completed | nobody; the request closes itself |

**Edge cases covered (unit tested, `tests/unit/test_onboarding_requests.py`)**
- **Sources by name.** A named source matches its registration, ignoring case and spaces. A name with no registration waits on the administrator, who links it to the registration once it exists. A source that is deleted or retired after filing needs attention.
- **Linking runs.**
  - The existing run that onboards a source into the domain is linked automatically. It must be top level, not archived, cancelled or failed, and in the same domain; the most advanced one is chosen.
  - Linking by hand refuses a run of another source, a run of another domain, and a table run of a multi-table run.
- **Multi-table runs.** A hub-and-spokes run stands where its slowest live table run stands ("2 of 6 table runs done"). Every role with a table at its step is in the waits, with the earliest step named first.
- **Runs that stop.**
  - A failed run needs attention at the step it failed.
  - A failed validation needs attention at Build.
  - A cancelled, archived or vanished run asks for another run.
  - A run of another domain is flagged.
- **Several sources.** A request with several sources is at its earliest part, and every role with work on any part sees it as its turn.
- **Request types.**
  - A request for a new domain links itself once a domain of that name exists. Filing one for a name that already exists is refused.
  - A domain-only request finishes when the model is approved.
  - A data product waits on a Gold run, so it is never done the moment it is filed.
- **Validation and duplicates.** Validation covers the title, the type, the priority, at least one source for a source request, and no source named twice. An open request for the same source and domain is reported, with links (409), unless "file it anyway".
- **Closing and reopening.**
  - Reject needs a reason and cannot be your own request.
  - Cancelled and rejected requests reopen; a finished request does not (file a new one).
  - A closed request cannot be edited, and the last source of a source request cannot be removed.
- **Before deployment.** Until V075 is deployed, the API answers 503 with "apply V075" instead of a Snowflake error.

**Who may do what** (checked by the API; `policy.RULES` marks these routes as checked by the endpoint):
- **File a request:** the domain's owners and stewards, or `DOMAIN.EDIT`, `RUN.CREATE` or `REQUEST.CHANGES`.
- **Link a registration:** `SOURCE.CONNECT`.
- **Link a run:** `RUN.CREATE` or `RUN.OPERATE`.
- **Cancel:** the requester or `ROLE.MANAGE`.
- **Reject:** `SOURCE.CONNECT`, `DOMAIN.EDIT` or `ROLE.MANAGE`.

Reads follow domain membership (`domains.can_see`). Requests waiting on a role appear in that role's My Work, in Enable (sources to enable) and in Design (the domain's requests). Notifications stay in-app.

## Where things live

| Path | What |
|---|---|
| `apps/web/app/os/**` | The shell layout, the ten screens, Help and the loading state |
| `apps/web/app/os/ados.css` | CBRE tokens and `os-*` classes, scoped under `.ados`, light and dark |
| `apps/web/components/os/**` | Shell (header, rail, persona), UI primitives, run picker, interactive bits |
| `apps/web/lib/os/data.ts` | Read-only adapters over the API, validation score, artifact grouping |
| `apps/web/lib/os/lifecycle.ts` | Workflow states mapped onto Intake, Enable, Ingest, Design, Generate, Validate, PR, Release |
| `apps/web/lib/os/access.ts` | The access list: screens, actions and personas against governance privileges |
| `apps/web/lib/os/session.ts` | The person's effective grants (or a previewed role's), approvers and the page guard |
| `apps/web/lib/os/personas.ts` | Ten personas, offered by privilege; the choice is kept in the `ados_persona` cookie |
| `apps/web/e2e/ados.spec.ts` | Read-only walk of every screen, plus a check that the classic shell is intact |

## Governance

The experience uses the platform's governance as it is: 48 privileges, 14 system roles and the approval policies (`services/governance/policy.py`). It creates no role and grants nothing. One file decides what each person is shown, `apps/web/lib/os/access.ts`:

- **Screens:** each screen lists the privileges that open it (any of them). The rail shows only screens the person may open. Opening any other screen by URL sends the person back to My Work with the reason, before the page reads anything.
- **Actions:** each button that changes something names the privilege it needs (`ACTIONS`).
  - A person who holds that privilege sees the button as normal.
  - A person who lacks it but holds `REQUEST.CHANGES` sees "Request: ...", and the tooltip names the approver role from the active policy. The classic page then routes the change to that role, as it always has.
  - Anyone else does not see the button.
- **Personas:** a person's privileges decide which personas they are offered (`PERSONA_REQUIRES`). A person with none of them gets Viewer. The chosen persona only orders the queue and the landing page.
- **Work queue:** My Work and Review list only the runs this person decides, may request a decision on, or operates. Holders of `APPROVAL.VIEW` see every gate.
- **Preview:** super admins only, by persona, from Admin → People (cookie `ados_preview_persona`; see Personas below). A preview changes only what is shown: data still loads as the signed-in person, and decisions are off.
- **The API still enforces everything.** Hiding a screen or a button controls what people see; it is not the security boundary. Reads are not restricted per role in the API (the Viewer role reads everything by design). Restricting reads by domain membership is a later backend step.

The Help screen (`/os/help`) prints the access list, marked for the person reading it.

## Personas and super-admin-only governance (V076)

People get **personas**, named bundles of the governance roles, instead of role toggles. A super admin assigns them in **Admin → People & personas** (`/os/people`). The platform grants the roles in those bundles (`CORE.USER_ROLE` stays where privileges come from), and "View as" offers each person only the personas they were given.

| Persona | Roles it grants |
|---|---|
| GDP Product Owner (data owner) | DATA_STEWARD |
| Data OS Administrator | PLATFORM_ADMIN |
| Data Designer | DATA_ENGINEER, MODEL_APPROVER, STTM_APPROVER, DQ_APPROVER |
| Forward Engineering Lead | DATA_ENGINEER, SKILL_OWNER |
| Data Developer | CODE_REVIEWER |
| QA & UAT Lead | QA_LEAD |
| Data Reliability Engineer | SUPPORT_ENGINEER |
| Knowledge & Skills Curator | DATA_STEWARD, SKILL_OWNER |
| Super admin | SUPER_ADMIN |

**Rules** (`services/governance/personas.py`, `tests/unit/test_personas.py`)
- Assigning personas grants every role their bundles hold. Removing a persona drops a role only when no other persona the person keeps still grants it. A role given directly, outside any persona, is kept.
- Editing a bundle moves everyone who holds it the same way. SUPER_ADMIN belongs only to the Super admin persona. GOVERNANCE_ADMIN is retired and cannot be put in a bundle.
- The last super admin cannot lose the Super admin persona.
- People from before personas keep their roles. People lists the personas their roles match, with a one-click "Give the matching persona". Anyone without a persona is a Viewer.

**Admin is a super admin's alone, enforced in the API** (`policy.SUPER_ONLY`)
- These privileges are honoured only alongside `*`: `ADMIN.VIEW`, `ADMIN.DEPLOY`, `CONFIG.EDIT`, `INTEGRATION.MANAGE`, `ROLE.MANAGE`, `AUDIT.VIEW` and `APPROVAL.VIEW`. Whatever role lists them, `effective_privileges` drops them for everyone else.
- A call that needs one of them is refused outright, never queued as a change request.
- The Admin rail entry (People & personas, Platform settings, Audit) shows to super admins only. Value is super-admin only too, because it reads the audit trail.
- Approvers still see their own Approvals queue.

**Preview moved off the top bar.** The "Preview as role" picker is gone. A super admin opens "See as <persona>" (or Viewer) in People. The same banner shows, decisions are off, and "Stop preview" returns to People.

**Existing people.** Deploying V076 gave nobody a persona. Their access is unchanged until a super admin assigns personas in People.

## Decisions taken in place

The five decisions people take most often happen on the `/os` screens themselves, in `components/os/decide.tsx`. Each one calls the server action the classic page already uses, so the API applies the same governance and keeps the same audit trail. Nothing new was added to the backend.

| Decision | Screen | Server action (existing) | Privilege (`ACTIONS`) |
|---|---|---|---|
| Approve or send back a gate (mapping, Data Mapping, data quality, code review), with a written justification | Review | `approveStage` (`app/runs/[runId]/actions.ts`) | the gate's own (`MAPPING.APPROVE`, `STTM.APPROVE`, `SODA.APPROVE`, `REVIEW.APPROVE`) |
| Sign off or reject QA (a rejection needs a note) | Release | `signOffQa` | `QA.SIGNOFF` |
| Publish the generated code as a pull request (draft by default), to the branch plan stored at generation | Release | `publishDbt` | `DBT.EDIT` |
| Make a skill version the candidate, or promote it to production (on an older version, that is the rollback) | Skills | `moveLabel` | `SKILL.EDIT` / `SKILL.RELEASE` |
| Verify a knowledge record; approve or reject agent proposals in the steward inbox | Knowledge | `verifyKnowledge`, `decideInbox` | `KNOWLEDGE.EDIT` |

How these behave:
- **People without the privilege:** if they may raise change requests, the button reads "Request …". The API's reply "Sent to <role> for approval" is shown as routed, in blue, not as an error. Anyone else does not see the control.
- **Gate send-back targets:** these come from the run's own allowed human transitions, so they always match the workflow graph. Cancelling a run is never offered as a send-back.
- **Previews:** every decision is disabled while an admin previews another role, because the API would act as the admin.
- **Creating a new skill version:** writing new content stays in the skills registry editor. Skill Studio links to it.

## Screens and their sources

| Screen | Reads |
|---|---|
| My Work `/os` | `/api/runs`, `/api/domains`, `/api/knowledge/overview` |
| Intake `/os/intake` | domains, `/api/sources/overview`, runs, `/api/domains/{id}/data-model`; starts the existing onboarding wizard |
| Enable `/os/enable` | `/api/sources/overview`, `/api/hub/ingestion-control` (secrets never shown), `/api/hub/pipelines` |
| Design `/os/design` | domain data model (shared `ErCanvas`), `/api/runs/{id}/mapping`, domain questions and contract |
| Build `/os/build?run=` | run state, `/audit`, `/validation`, `/dbt`, `/skills` of one run |
| Review `/os/review?run=` | the same bundle, with the gate at 80% and no errors; every human gate across domains |
| Skills `/os/skills?skill=` | `/api/skills`, `/api/skills/{name}`, `/api/skills/{name}/diff`, skill proposals |
| Release `/os/release?run=` | run bundle, `/api/runs/{id}/qa*`, `/api/source-control/prs`, knowledge feed |
| Knowledge `/os/knowledge` | `/api/knowledge*` (overview, list, versions, usage) |
| Run & DQ `/os/run` | `/api/ops/summary`, `/api/ops/dags`, `/api/ops/incidents`, quality summary and executions |

Actions such as approving, editing a mapping, publishing a PR or versioning a skill are not duplicated. Each one links to the classic page that already performs it.

## Validation score

The score is the share of the latest evaluated checks that passed (`SUMMARY` is excluded; `SKIPPED` checks are not evaluated). A run passes the gate at 80% or higher with zero errors. A check with an error blocks the run whatever the score.

## Gaps for a later backend step

The guide describes some concepts that have no backend record yet. These screens show only what exists today:

- **Request lineage:** there is no `asset` / `asset_links` record for a product request. Intake previews the links but does not persist them.
- **Developer edits after generation:** Release lists the generated files. A diff of generated code against merged code needs Git provider hooks.
- **Skill test results:** results appear when `eval_json` is recorded on a skill version. Most versions have none yet.
- **UAT signoff:** this is distinct from QA signoff and has no field yet.
