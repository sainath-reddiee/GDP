# Agentic Data OS experience (`/os`)

A persona-led front end for the platform, built from the CBRE ADOS UX package (ten screens, one shell, one eight-step lifecycle). Every signed-in page, the `/os` screens and all the classic pages alike, opens in the same shell. The rail has the package's ten entries, plus Admin for admins. Every page is a tab under one of them. No classic feature was rebuilt or removed: the classic pages render unchanged inside the new frame.

Every screen reads live platform data through the endpoints the classic pages already use. There is no sample data. When an endpoint fails or has nothing to return, the screen shows the reason or an empty state.

## The rail: ten entries, every page a tab

| Entry | Tabs |
|---|---|
| Home | Home (`/os`); Inbox (`/os/work/inbox`) and Overview (`/os/work/overview`) keep their addresses but are not tabs |
| Intake | Requests board (`/os/intake`); the form is `/os/intake/new`; the older Requests and Request history lists keep their addresses |
| Enable | Source readiness, Onboard source, Onboard domain, Integrations (the Data OS Administrator's alone) |
| Design | Designer studio (the Design board), Domains, Stores (the six stores, with their own store tabs) |
| Build | Ready to build, Workbench (the Build board; and every `/runs/[id]` workspace), Lineage, Code, Run history (with the Silver and Gold pipeline lists) |
| Review | Gates, Approvals, Audit |
| Skills | Skill studio, Registry (including proposed skills) |
| Release | Release, Source control, QA testing |
| Knowledge | Knowledge hub, Enterprise Memory |
| Run & DQ | Run & DQ, Quality center, Monitoring, Incidents, Resolutions, Known fixes, Permanent fixes, Tickets (and Jira) |
| Admin | Admin (admins only) |

Each person sees only the entries and tabs their privileges open; an entry opens its first open tab. Every one of the 79 page routes belongs to exactly one tab (checked against the access list). In the new shell, the tab bar replaces the classic workspace tabs, so a page never shows two tab bars.

The header holds the brand, Copilot, "View as" (and, for admins, "Preview as role"), the "New" menu and the account menu (work-role switch, appearance, sign out). There is no domain filter, no search and no Ctrl K palette: the domain scope is retired (`lib/domain-scope.ts` always answers "all"; pages narrow by `?domain=` alone). The Copilot button (for `AI.USE`) opens the Copilot page, the chat workspace (`/os/work/copilot`); Ctrl J still opens the quick drawer for a question about the page on screen. There is no floating launcher and no workspace pill.

**How it is wired**
- `middleware.ts` passes each signed-in request's path to the root layout (header `x-ados-path`, set by the server, never taken from the client).
- The root layout looks the path up in the access list. A page outside the person's role (or the previewed role) goes back to Home with the reason, before it renders.
- `components/os/chrome.tsx` draws the frame. The classic page components themselves are unchanged.

**Classic layout (admins only).** Admin has a "Classic layout" switch (cookie `ados_classic`). The root layout honours it only for holders of `ADMIN.VIEW` or super admins. With it on, the classic pages get the old sidebar and top bar; `/os` keeps its shell either way. The e2e checks of the classic layout (`command-center.spec.ts`) set this cookie.

**Landing.** Everyone lands on Home (`/os`) after signing in and on `/`, whatever their role (`lib/os/home.ts`).

## Home (`/os`)

The studio at a glance, with no hero. Everything on it opens the stage it is at with that workflow selected.

- **Onboarded domains.** A row of cards below the workflows (the actions come first): each domain's tables, memory and open workflows, "N need you" where the viewer has work, Gold tables and MDM role. Domains with work for the viewer come first; each opens its domain workspace.

- **Workflows.** Each row is a request: its type and intent ("Enhance source"), domain, priority, overdue, and a step track (Domain, Enable, Design, Build, Review, Done) with the step it is at and "Waiting on <role> · 2d". The role is the one the wait names ("Model approver: approve the field mapping"). When the viewer's role acts next the row says "Your turn" with a button in the step's own words ("Approve the field mapping", "Land YARDI"; `waitCta`), which opens where that step is worked; a step another role owns reads "Waiting on <role>". A row expands to its sources and their runs. "Open", the title and the step track open the workflow on Intake, selected (`lib/os/work.ts` `workflowHref`); from there "Open where it is" goes to the stage it is at (`stageHref`: Design and Build open its run, Enable and Review their screens).
- **Views and filters**, all in the URL so a view can be shared: Active (open), Action required (open and waiting on a privilege the viewer holds, leaving out steps an agent is still running such as a landing or a validation in progress; `requestNeedsMe`; the default when it has any), All; type (Domain, Source, Product); New or Enhance.
- **Layout.** Platform actions on the left, a wider right column (330, 364 and 420 px at the lg, xl and 2xl breakpoints) for Start a workflow. The toolbar (title, search, views and filters) stays pinned under the header while the workflows scroll; the right column stays in view when it is shorter than the page.
- **Search by meaning** (`components/os/platform-search.tsx`, `GET /api/search`, below). Typing filters the workflows by meaning (best match first, "show all N" when the view hides some) and opens results across workflows, runs, domains, sources, tables and knowledge, grouped by kind, each with why it matched. Arrows move, Enter opens, Escape closes, "/" focuses it. It understands intent: "waiting on me", "my approvals", "failed runs", "property tables", "what is net rentable area".
- **One queue for the whole platform** ("Platform actions"). Besides the workflows, every other action that waits on the viewer is a row in the same list, with the same "Your turn" and a specific button (`lib/os/actions.ts`, `PlatformActionRow`), read from each screen's own endpoint and filtered by their privileges: run gates and failed or failed-validation runs no open workflow already shows; change requests they may decide; Airflow known fixes to approve; domain models in review (not their own) and model drafts of theirs or opened by the deploy (a domain pack update); data quality suites in review; QA packs to approve; knowledge proposed for review; skill proposals; incidents assigned to them. Each row is tagged with where it is worked (Run gate, Approval, Review, Knowledge, Operations). The queue puts what needs attention first (failed runs and validations, workflows flagged), then the viewer's workflows, then the other actions by urgency; the Type filter's "Platform" option shows only these, and the view counts include them.
- **Start a workflow.** Data domain, Data source and Data product, each New or Enhance, open the Intake form (`/os/intake/new`) with the type and intent chosen. Everyone sees the panel; the buttons open for people who may file a request (the Intake screen's privileges) and are disabled with the reason for the rest. Onboarding directly is on Enable (Onboard domain, Onboard source), not repeated here. Data products are coming soon.

## Stage boards: Intake, Design, Build

One pattern on each stage: tabs **Selected | Action required | Active | Completed** in the URL (`?view=&run=` or `&request=`), workflow cards with their step track and what the current step is doing, and the selected workflow below (`components/os/stage-board.tsx`, `lib/os/work.ts`).
- **Intake** lists requests (Action required: waiting on a privilege the viewer holds; Completed: done, cancelled or rejected). Selected shows the request's steps, who acts now with a button in the step's own words (for an Enhance source request waiting on the Data Designer, "Start enhancement run" right there), its sources with what each should add, and the latest history. On the right: New or Enhance per kind, and "In flight: New N · Enhance M", each count filtering the board (`?intent=new|enhance`).
- **The form** (`/os/intake/new`, source requests): the request details first (title, priority, needed by, business outcome), then the domain and the sources on the platform grouped by the domains their runs feed (the chosen domain's sources first, the others collapsed by domain, then "Not in a domain yet"), then Submit. New offers every registered source and a new one by name, and suggests Enhance for a source that already feeds the domain. Enhance offers only the sources feeding the domain and asks what to add from each. "Where this goes" on the right shows the route for what is picked: the New source flow or the Enhance flow, with a skipped step marked.
- **Design** lists runs whose step is Design; Completed are past it. Selected shows the run's eight steps and **Profile | Model | Map** for its domain (opening on the one its state is at): the source profile, the domain model with open questions and artifacts, and the mapping proposals with the gate decision. `?domain=` still selects that domain's run.
- **Build** lists runs at Generate or Validate; Completed are past it. Selected is the workbench: "N of 8 steps done", the steps, the agents, the artifacts, run context, and Gates and quality.
- Action required on runs: a gate the viewer decides or may request (all gates for `APPROVAL.VIEW`), a failed validation or run for `RUN.OPERATE`, or a running step the persona works.

## Search by meaning

`GET /api/search?q=&kinds=&limit=` (`apps/api/app/search_api.py`, `services/search/resources.py`) is hybrid retrieval:
- **Semantic:** Cortex Search, keyword plus vector with reranking (embedding model `snowflake-arctic-embed-l-v2.0`), over `CORE.RESOURCE_SEARCH` (`snowflake/cortex_search/resource_search.sql`: workflows, domains, sources, runs, target tables) and the existing `CORE.KNOWLEDGE_SEARCH` (glossary, rules, patterns). The services refresh themselves within 10 minutes.
- **Keyword:** the same resources scored on their names and descriptions with the platform's synonyms (client is customer, ingest is land, approval is review, broken is failed, and so on), word stems and name prefixes, so an exact name always ranks first.
- **Fusion:** reciprocal rank fusion of the two rankings; a result found by both ranks highest. Each result says why it matched ("name: property", "client ~ customer", "similar meaning").
- **Intent:** "waiting on me" keeps workflows and runs waiting on a privilege the caller holds; "failed" keeps failed runs and workflows needing attention; kind words ("domains", "runs", "tables", "glossary") filter by kind. These words are not searched for.
- **Visibility:** results follow domain membership (`domains.can_see`), as everywhere else.
- **Fallback:** before the search service is deployed, or without Cortex, the keyword half answers alone (mode "keyword", shown on the results).

## Onboarding requests (V075)

A data owner files a request on Intake: sources for a domain, or a new domain (named, never picked from the domains that exist), each new or enhanced (V078, below). Data product requests are a "coming soon" placeholder on Intake. The request then travels to whoever acts next. Nobody types its stage in. Every read works it out again from the domain, each source's registration and health, and the runs that onboard them (`services/onboarding/requests.py`).

| Stage | When | Who acts (privilege) |
|---|---|---|
| Domain | the domain does not exist yet (Data OS Administrator, `DOMAIN.ONBOARD`), or was deleted or retired (Data steward, `DOMAIN.EDIT`, restores it) | as named |
| Enable | a source is not registered, is unreachable, is not landed, or its run in the domain is not started | Data OS Administrator (`SOURCE.CONNECT`) |
| Design | no approved domain model, or a run being profiled, mapped and given its transformations, up to the approved STTM | Data Designer (`PROFILE.RUN`, `MAPPING.DECIDE`, `STTM.EDIT`) and the gate approvers (`MAPPING.APPROVE`, `STTM.APPROVE`) |
| Build | from the approved STTM: data quality checks, code, validation and the pull request | Forward Engineering Lead (`RUN.OPERATE`), with Build end to end |
| Review | code review | Data Developer (`REVIEW.APPROVE`) |
| Done | every source's run is approved or completed | nobody; the request closes itself |

**Edge cases covered (unit tested, `tests/unit/test_onboarding_requests.py`)**
- **Sources by name.** A named source matches its registration, ignoring case, spacing and punctuation. A name with no registration waits on the administrator, who links it to the registration once it exists. A source that is deleted or retired after filing needs attention.
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
- **Reject:** `SOURCE.CONNECT`, `DOMAIN.ONBOARD`, `DOMAIN.EDIT` or `ROLE.MANAGE`.
- **Add or remove files:** the requester, the domain's owners and stewards, or `DOMAIN.ONBOARD` or `DOMAIN.EDIT`.

**Files on a request (V079).** A domain request (New or Enhance) takes files of any format: a data dictionary, a model export, a glossary, a sample domain JSON, a PDF (at most 10 files of 50 MB per upload). The form offers "Download sample domain JSON" (the starter domain pack, `PACK_TEMPLATE`, valid against the pack validator) to share with whoever describes the domain. Files are kept in `@CORE.DOCUMENT_FILES` under `requests/<request id>/` (`CORE.ONBOARDING_REQUEST_FILE`) and listed, with downloads, on Intake Selected and the request page. Once the request's domain exists (at once for an Enhance domain request, when it is linked for a new one), each file is handed to the domain's documents (`domain_sources_api.register_upload`): read there and its knowledge proposed for the steward's review ("Files added to the domain's documents" in the history). Until V079 is deployed, the request shows no files and uploading answers 503 with "apply V079".

**New or enhance (V078).** `INTENT` is NEW or ENHANCE (NEW for requests filed before it).
- **Enhance a domain:** pick a domain already on the platform and say what its model needs. The request waits at Design on the Data Designer (`MODEL.EDIT`) until a model version newer than the one approved at filing (`BASE_MODEL_VERSION`) is approved; its sources then follow as usual.
- **Enhance a source (the delta flow):** pick a source already feeding the domain and say what to add; a source not on the platform is refused (file it as a new source). Its steps are Scope, Impact & delta mapping (Data Designer), Regenerate & regression (Forward Engineering Lead), Review, Done; "Land the delta" (Data OS Administrator) appears only while the source is unreachable or not landed. With the source landed and no run yet, the request waits at Design on `RUN.CREATE`: the Data Designer starts the enhancement run (`POST /api/onboarding-requests/{id}/sources/{item}/enhance-run`, a new run of the onboarded source in the domain carrying what to add), and the run's gates follow as usual (phase "Impact and delta mapping"). Only a run started after the request was filed is linked automatically, never the run that first onboarded the source. No "Source onboarded" milestone is recorded. On a new source request, Intake suggests Enhance when a picked source already feeds the domain.
- Until V078 is deployed, every request reads as NEW and filing an Enhance request answers 503 with "apply V078".

Reads follow domain membership (`domains.can_see`). Requests waiting on a role appear under Action required on that role's Home, in Enable (sources to enable) and in Design (the domain's requests). Notifications stay in-app.

**Phases, in the owner's words.** Next to the stage, a request and each of its sources carry a phase (`requests.phase`):

| Phase | When |
|---|---|
| Waiting on the domain | the domain does not exist yet |
| Waiting on admin | a source is not registered, reachable or landed |
| Source onboarded | the source is ready and waits for the designer's run |
| Ready to build | a data product, waiting for its Gold run |
| Designer profiling and modelling | the run is being profiled, mapped and approved, or the domain model is not approved |
| Design stored | the design is approved (profiles, model, mapping and checks in the stores) and waits for code generation |
| Building | code generation and validation |
| Validation flagged | validation failed the gate; improve and validate again |
| In review, Done, Closed | code review, finished, cancelled or rejected |

A request shows the earliest phase of the parts at its stage. Two milestones are recorded as automatic events when a source's status changes: `SOURCE_ONBOARDED` when a source the administrator had (missing, unreachable or not landed) becomes ready, and `DESIGN_STORED` when its design is approved and the build can start. Attaching a source that is already onboarded records only `SOURCE_ADDED`.

**Who does what.**
- The **data owner** files the request on Intake. Intake lists the sources already feeding the domain (from its runs), then the other registered sources, ready ones first. A source not on the platform is named under "Request a new source" and goes to the administrator.
- The **Data OS Administrator** onboards sources, alone: registering a source, landing it and starting its run in the domain. The onboarding wizard (`/dashboard/onboarding`) is a tab of Enable and opens only for `SOURCE.CONNECT`, which only `PLATFORM_ADMIN` holds (system roles version 9 moved it from `DATA_ENGINEER`, once, with a `PRIVILEGE_MOVED` governance event). Everyone else asks for a source on Intake; the classic "Onboard source" buttons become "Request a source" for them. Domains are the administrator's too: Enable, Onboard a domain (`/os/enable/new-domain`), opens only for `DOMAIN.ONBOARD`, a privilege only `PLATFORM_ADMIN` holds (system roles version 10 grants it once). Creating, drafting or importing a domain (`POST /api/domains`, `/draft`, `/import`) needs it; data stewards keep `DOMAIN.EDIT` for editing domains that exist. Enable lists the domain requests waiting, with "Onboard this domain", which opens the wizard with the request named; a domain of the requested name is attached to the request on its own. Old links to the domain wizard (`/dashboard/onboarding?type=domain`, `/os/design/new-domain`) go there.
- The administrator works from Enable. Enable is configuration only, with no table catalog. Each source shows its connection, credential reference (only whether one is set), landing target, schedule, SnapLogic master and child pipelines, and status. Sources asked for by requests come first, with "Onboard as" buttons for the wizard's three ways in (Snowflake share, relational database, files). The wizard opens with the request named and the source name filled in (`YARDI_VOYAGER` for "Yardi Voyager"); a source registered under that name is attached to the request on its own (names match ignoring case, spacing and punctuation).
- The **Data Designer** profiles and models; the approved design is kept in the stores.
- The **Forward Engineering Lead** builds end to end and must pass the validation gate.

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

- **Screens:** each screen lists the privileges that open it (any of them). The rail shows only screens the person may open. Opening any other screen by URL sends the person back to Home with the reason, before the page reads anything.
- **Actions:** each button that changes something names the privilege it needs (`ACTIONS`).
  - A person who holds that privilege sees the button as normal.
  - A person who lacks it but holds `REQUEST.CHANGES` sees "Request: ...", and the tooltip names the approver role from the active policy. The classic page then routes the change to that role, as it always has.
  - Anyone else does not see the button.
- **Personas:** a person's privileges decide which personas they are offered (`PERSONA_REQUIRES`). A person with none of them gets Viewer. The chosen persona only orders the queue.
- **Work queue:** Home and Review list only the runs this person decides, may request a decision on, or operates. Holders of `APPROVAL.VIEW` see every gate.
- **Preview:** super admins only, by persona, from Admin → People (cookie `ados_preview_persona`; see Personas below). A preview changes only what is shown: data still loads as the signed-in person, and decisions are off.
- **The API still enforces everything.** Hiding a screen or a button controls what people see; it is not the security boundary. Reads are not restricted per role in the API (the Viewer role reads everything by design). Restricting reads by domain membership is a later backend step.

The Help screen (`/os/help`) prints the access list, marked for the person reading it.

## Personas and super-admin-only governance (V076)

People get **personas**, named bundles of the governance roles, instead of role toggles. A super admin assigns them in **Admin → People & personas** (`/os/people`). The platform grants the roles in those bundles (`CORE.USER_ROLE` stays where privileges come from), and "View as" offers each person only the personas they were given.

| Persona | Roles it grants |
|---|---|
| GDP Product Owner (data owner) | DATA_STEWARD |
| Data OS Administrator | PLATFORM_ADMIN |
| Data Designer | DATA_DESIGNER (V077), MODEL_APPROVER, STTM_APPROVER, DQ_APPROVER |
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
- These privileges are honoured only alongside `*`: `ADMIN.VIEW`, `ADMIN.DEPLOY`, `CONFIG.EDIT`, `ROLE.MANAGE`, `AUDIT.VIEW` and `APPROVAL.VIEW`. Whatever role lists them, `effective_privileges` drops them for everyone else. (`INTEGRATION.MANAGE` was among them until the Data OS Administrator took over integrations on Enable.)
- A call that needs one of them is refused outright, never queued as a change request.
- The Admin rail entry (People & personas, Platform settings, Audit) shows to super admins only.
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
| Home `/os` | `/api/onboarding-requests?status=ALL`, `/api/domains` |
| Intake `/os/intake` | `/api/onboarding-requests?status=ALL`, `/api/onboarding-requests/{id}` |
| New request `/os/intake/new` | domains, `/api/sources/overview`, runs, `/api/domains/{id}/data-model`; files a request (sources go to the administrator) |
| Enable `/os/enable` | `/api/sources/overview`, `/api/hub/ingestion-control` (secrets never shown), `/api/hub/pipelines` |
| Design `/os/design` | runs, `/api/sources/overview`, domain data model (shared `ErCanvas`), `/api/runs/{id}/mapping`, domain questions and contract |
| Build `/os/build` | runs; for the selected run its state, `/audit`, `/validation`, `/dbt`, `/skills` of one run |
| Review `/os/review?run=` | the same bundle, the gate from `/validation` (`gate`), every attempt's score, Validate again in place; every human gate across domains |
| Run history `/os/history` | `/api/runs` (all pipelines), `/api/run-history` |
| Request history `/os/requests/history` | `/api/onboarding-requests?status=CLOSED` |
| Skills `/os/skills?skill=` | `/api/skills`, `/api/skills/{name}`, `/api/skills/{name}/diff`, skill proposals |
| Release `/os/release?run=` | run bundle, `/api/runs/{id}/qa*`, `/api/source-control/prs`, knowledge feed |
| Knowledge `/os/knowledge` | `/api/knowledge*` (overview, list, versions, usage) |
| Run & DQ `/os/run` | `/api/ops/summary`, `/api/ops/dags`, `/api/ops/incidents`, quality summary and executions |

Actions such as approving, editing a mapping, publishing a PR or versioning a skill are not duplicated. Each one links to the classic page that already performs it.

## Validation gate

The score is the share of the evaluated checks that passed (`SUMMARY` is excluded, `SKIPPED` checks are not evaluated, warnings count as passed). The gate is two platform rules (`services/common/rules.py`), editable in Admin, Rules, under "Validation gate", and per domain and pipeline like every rule:

- `validation.min_score` (default 80): the minimum score.
- `validation.block_on_errors` (default true): any error also blocks, whatever the score.

`VALIDATE_DBT` enforces it (`checks.gate`): below the minimum, or with an error while errors block, the run moves to `VALIDATION_FAILED` with the reasons ("score 72% is below the 80% minimum") in its event, its tool call and the `SUMMARY` row (`RESULT_JSON.gate`). The comparison uses the exact score, so 79.96% does not pass an 80% gate. `GET /api/runs/{id}/validation` returns the run's gate, so the pages draw the same line. Review shows every attempt's score ("Improved 72% → 93% over 3 attempts") and "Validate again" runs the checks on the current generation in place (`RUN.OPERATE`; others can request it).

## Code standards agent

An independent agent reviews every generated dbt project against the written standards (`services/validation/standards.py`). It never edits code.

- **What it reads:** the generated project (models, macros, tests, YAML, Soda checks; the STTM exports are left out), at most `validation.standards_max_files` files (default 40) within a character budget. It reads them against its own skill, `CODE-STANDARDS-REVIEW` (`snowflake/skills/code-standards`), plus the generation playbooks bound to the run's DBT stage and any standards a domain binds to the `STANDARDS` stage in Admin.
- **What it reports:** a verdict for each of six areas: naming and layering, casts and data types, keys and joins, tests, documentation and sources, and SQL style and safety. Each finding cites the rule, the file and a line.
- **How it moves the gate:** inside `VALIDATE_DBT`, each area is one check (`STANDARDS_NAMING`, ...), replacing the old always-passing `SKILL` row, so the agent's findings move the score and the minimum-score rule applies. With `validation.standards_block` off, its errors are recorded as warnings. With `validation.standards_review` off, the agent does not run and the `SKILL` row is recorded as before.
- **When Cortex fails:** the review becomes one `SKIPPED` check with the reason. Skipped checks are not evaluated, so the gate stands on the other checks.
- **Running it alone:** `CORE.REVIEW_STANDARDS` (`POST /api/runs/{id}/standards`, "Review standards" on Review, `RUN.OPERATE`) reports the areas without recording a validation or moving the run. Every review records its tool call and its Cortex cost (agent `CODE_STANDARDS`).
- **Improving it:** a new version of `CODE-STANDARDS-REVIEW` in Skill Studio, promoted, changes how the next review judges. A better generation playbook, after regenerating, changes the code it judges. Review links to the standards skill when a standards area failed.

## Build: ready to build, end to end

Sourcing moved to Intake and Enable, and modelling and mapping to Design, so Build starts where design ends. Build, Ready to build (`/os/build/ready`, the Build rail entry) lists every live Silver run, table by table, grouped by domain (`GET /api/build/readiness`, `services/workflow/readiness.py`):

- **Design readiness:** each run shows four checks: the domain model approved, the field mapping approved, the transformations (STTM) approved, and the data quality checks approved. Readiness is the share of the first three. Data quality approval runs in parallel with the build, so it is shown but never waited for.
- **Status:** Ready to build; Building; Validation flagged; In review; Model not approved; In design; Being onboarded; Failed; Built (hidden unless asked for). A run not ready says who acts next and links where they act.
- **Linked:** each run names the onboarding request it serves, its source and, for a table run, the run it belongs to; a flow ribbon counts the runs at Intake, Enable, Design, Build and Review.
- **Build end to end:** autopilot (`/api/runs/{id}/autopilot`, `services/workflow/autopilot.py`) runs every automatic step of a ready run in order: generate the data quality checks and approve them, generate the code, validate it with the review and standards agents, then commit it and open the pull request (`POST /api/runs/{id}/dbt/publish`, when GitHub publishing is set up). It stops at code review for the Data Developer, unless the person who started it may sign off QA (`QA.SIGNOFF`) and review code (`REVIEW.APPROVE`). The checks are approved by autopilot on behalf of the DQ approver while the setting "Build end to end approves the data quality checks" (`AUTOPILOT_APPROVES_DQ`, Admin, on by default) is on; the approval's justification says so. Every other step passes the same governance check as a click (`RUN.OPERATE`); a preview of another role disables it. "Build all ready" starts every ready run of a domain. The Workbench offers the same button for a ready run.
- **The Workbench** is one run's detail, opened from the board. The Silver and Gold pipeline lists left Build's tabs: they open from Run history ("Silver pipeline list", "Gold pipeline list") and stay guarded as before.

## Enable: one way in per object

Onboarding a source or a domain is reached from Enable's tabs only (Onboard source, Onboard domain); Source readiness's header keeps just "Open Source Store". **Integrations** (`/os/enable/integrations`, `INTEGRATION.MANAGE`) is the Data OS Administrator's: code repositories, Jira, Airflow, Datadog, incident routing and cases, the same screen super admins have in Platform settings. `INTEGRATION.MANAGE` is no longer super-admin-only (`policy.SUPER_ONLY`); setting up GitHub publishing still needs a super admin (`ADMIN.DEPLOY`: it deploys a procedure and stores the token). Without the privilege a button shows disabled with the privilege it needs; with only `REQUEST.CHANGES` it reads "Request approval: ...". A source a request asks for has one "Onboard source for request" button (the wizard asks how it arrives: share, database or files), a registered one not landed "Resume landing" and an unreachable one "Recheck access", both on its run's source step ("Open in Source Store" when it has no run yet). A domain asked for reads "Onboard domain for request". The duplicate kind tiles, the second Source Store links and "Manage pipelines" were removed; the wizards use "Check configuration", "Save configuration", "Upload pack and contract", "Validate pack" and "Import domain pack". Every Enable page's eyebrow reads "Enable / Data OS Administrator".

## Domain packs of governed domains

A domain whose model was approved in the Model Workbench is governed: the deploy does not re-apply its pack's tables, since that would undo approved changes (`drop_governed`). When its `domain/<name>/domain_pack.json` adds, drops or reshapes a table or column, the deploy saves the pack as a **draft model version** (`seed_knowledge.pack_drafts`): the approved model with the pack applied, tables the pack no longer has retired, note "Pack update from domain/<name>/domain_pack.json (<digest>)". A Data Designer reviews and approves it in the Model Workbench, and approval re-registers the tables. A draft already open is left alone (the log says so), and a pack proposed once is not proposed again after a rejection. Wording-only changes open no draft. Pack knowledge the pack no longer has is retired when the deploy wrote it and nobody edited it. `infrastructure/contracts_to_packs.py` no longer regenerates PROPERTY, whose pack is maintained by hand (name it on the command line to overwrite).

## Testing the flows

`tests/unit/test_request_journeys.py` walks each flow persona by persona without Snowflake: New source (Product Owner, Data OS Administrator, Data Designer, Forward Engineering Lead, Data Developer), a source already ready (straight to the Forward Engineering Lead), and Enhance source. At each step it checks the request's stage and the privilege it waits on, that the persona's bundle holds it and every endpoint privilege the step calls, that the graph allows the move, and what the requester's history records. `tests/integration/run_request_journey.py` runs the New source flow on DEV over HTTP, each step as its persona (`X-AIP-Persona`). `docs/uat-flows.md` is the click-through checklist.

## Run and request history

- **Build, Run history** (`/os/history`): every run of both pipelines, finished or not, filtered by outcome, pipeline, domain and text. Each row shows how the run ended, its latest validation score with the number of attempts and where it started, and who decided its gates (`GET /api/run-history`). "Recovered" counts runs whose latest attempt passed after an earlier one failed. It is open to everyone signed in; viewers read it without links into the Workbench.
- **Intake, Request history** (`/os/requests/history`): finished requests (done, cancelled, rejected), with how they ended, who closed them and how long they took. Each opens its whole timeline.
- The full audit trail stays in Admin, Audit (super admins only).

## Demo paths

Help shows two walkthroughs built from live records, with no fixtures:

1. **Forward Engineering Lead, happy path:** the most advanced run that passed validation, from Home through the Workbench and the validation gate to the pull request and code review.
2. **Onboarding with people in the loop:** the open request furthest along, from filing through the administrator (skipped on its own when the sources are already onboarded), the designer, build and validation, improving a skill and validating again when flagged, to the pull request, QA and release.

Each step names the persona who acts and opens the real run or request. Scores are whatever the agents find.

## Gaps for a later backend step

The guide describes some concepts that have no backend record yet. These screens show only what exists today:

- **Request lineage:** there is no `asset` / `asset_links` record for a product request. Intake previews the links but does not persist them.
- **Developer edits after generation:** Release lists the generated files. A diff of generated code against merged code needs Git provider hooks.
- **Skill test results:** results appear when `eval_json` is recorded on a skill version. Most versions have none yet.
- **UAT signoff:** this is distinct from QA signoff and has no field yet.
