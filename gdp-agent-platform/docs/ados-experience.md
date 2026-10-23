# Agentic Data OS experience (`/os`)

A persona-led front end for the platform, built from the CBRE ADOS UX package (ten screens, one shell, one eight-step lifecycle). It sits beside the classic app and changes none of it: the root layout hides the classic sidebar, top bar and tabs only under `/os`, and the classic sidebar gains one "Agentic Data OS" entry.

Every screen reads live platform data through the endpoints the classic pages already use. There is no sample data. When an endpoint fails or has nothing to return, the screen shows the reason or an empty state.

## Where things live

| Path | What |
|---|---|
| `apps/web/app/os/**` | The shell layout, the ten screens, Help and the loading state |
| `apps/web/app/os/ados.css` | CBRE tokens and `os-*` classes, scoped under `.ados`, light and dark |
| `apps/web/components/os/**` | Shell (header, rail, persona), UI primitives, run picker, interactive bits |
| `apps/web/lib/os/data.ts` | Read-only adapters over the API, validation score, artifact grouping |
| `apps/web/lib/os/lifecycle.ts` | Workflow states mapped onto Intake, Enable, Ingest, Design, Generate, Validate, PR, Release |
| `apps/web/lib/os/personas.ts` | Seven personas (a view preference in the `ados_persona` cookie; privileges are unchanged) |
| `apps/web/e2e/ados.spec.ts` | Read-only walk of every screen, plus a check that the classic shell is intact |

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
