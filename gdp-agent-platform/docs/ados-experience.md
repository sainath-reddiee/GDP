# Agentic Data OS experience (`/os`)

A persona-led front end for the platform, built from the CBRE ADOS UX package (ten screens, one shell, one eight-step lifecycle). Every signed-in page, the ten `/os` screens and all the classic pages alike, opens in the same shell with one sectioned, privilege-filtered rail. No classic feature was rebuilt or removed: the classic pages render unchanged inside the new frame, with their own workspace tabs.

Every screen reads live platform data through the endpoints the classic pages already use. There is no sample data. When an endpoint fails or has nothing to return, the screen shows the reason or an empty state.

## One shell, five sections

| Section | Rail entries (each shown only to the privileges in the access list) | Classic pages it covers |
|---|---|---|
| Work | My Work, Inbox, Overview, Copilot | `/os`, `/work`, `/dashboard`, `/dashboard/copilot` |
| Workflow | Intake, Enable, Design, Build, Review, Skills, Release | onboarding wizards, every `/runs/[id]` workspace (under Build), `/skills`, source control |
| Assets | Pipelines, Stores, Knowledge | `/runs`, `/gold`, lineage, code, the six stores, `/domains`, Enterprise Memory |
| Operate | Run & DQ, Quality, Operations | quality center, QA testing, monitoring, incidents, fixes, tickets, Jira |
| Govern | Approvals, Audit, Value, Admin | `/approvals`, `/audit`, `/value`, `/admin` |

The header carries what the classic top bar did: the domain scope, global search (Ctrl K), the "New" menu, and the account menu (work-role switch, appearance, sign out). It also has "View as" and, for admins, "Preview as role".

**How it is wired**
- `middleware.ts` passes each signed-in request's path to the root layout (header `x-ados-path`, set by the server, never taken from the client).
- The root layout looks the path up in the access list. A page outside the person's role (or the previewed role) goes back to My Work with the reason, before it renders.
- `components/os/chrome.tsx` draws the frame. The classic page components themselves are unchanged.

**Classic layout (admins only).** Admin has a "Classic layout" switch (cookie `ados_classic`). The root layout honours it only for holders of `ADMIN.VIEW` or super admins. With it on, the classic pages get the old sidebar and top bar; `/os` keeps its shell either way. The e2e checks of the classic layout (`command-center.spec.ts`) set this cookie.

**Landing.** With `ADOS_LANDING=1`, signing in and opening `/` land on `/os` (mission control). The default is off, so people land on the classic My Work (`/work`), which now also opens in the new shell.

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
- **Preview as role:** super admins and governance admins can switch the UI to any role's effective privileges (cookie `ados_preview_role`). A banner shows while a preview is on. A preview changes only what is shown: data still loads as the signed-in person, and actions still run as them.
- **The API still enforces everything.** Hiding a screen or a button controls what people see; it is not the security boundary. Reads are not restricted per role in the API (the Viewer role reads everything by design). Restricting reads by domain membership is a later backend step.

The Help screen (`/os/help`) prints the access list, marked for the person reading it.

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
