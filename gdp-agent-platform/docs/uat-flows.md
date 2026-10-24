# UAT: the onboarding flows, click by click

Three flows to check on DEV, each step done as the persona that owns it. Give one test user every persona below in
Admin, People & personas, then switch with "View as" in the account menu (or have one tester per persona). A super
admin can also follow along with "See as <persona>" in People, but decisions are off in that preview, so each step
must be done by someone working as the persona.

Before starting: V078 and V079 applied, the API redeployed, the seed run, GitHub publishing set up (Admin,
Integrations) and the demo source `DEV_AIP_DEMO_SOURCE.CRM` reachable. The same New source flow runs unattended with
`python tests/integration/run_request_journey.py`.

## Flow A: a new source, end to end to a pull request

| # | Persona | Where | Do | Expect to see |
|---|---|---|---|---|
| 1 | GDP Product Owner | Home, Start a workflow, Data source, New | Fill the request details, pick the domain, add the source by name (one not on the platform yet), Submit | Intake opens with the request selected. Step track: Enable current. Who acts now: "Data OS Administrator: register and connect <source>" |
| 2 | Data OS Administrator | Home, Action required | Open the request, "Do it" | Enable opens on the source |
| 3 | Data OS Administrator | Enable | Register the source, validate access, land the tables, start its run in the domain | The request reads "Source onboarded" in its history; Who acts now names the Data Designer |
| 4 | GDP Product Owner | Home, Active | Open the request | Phase "Source onboarded", waiting on the Data Designer; nothing for the Product Owner to do |
| 5 | Data Designer | Home, Action required, then Design | Profile, identify the domain, generate the mapping, decide and approve it, generate and approve the STTM | Design board moves the run through Profile, Model, Map. After the STTM approval the request reads "Design stored" and moves to Build |
| 6 | Forward Engineering Lead | Build, Selected (or Ready to build) | Build end to end | Steps run in order: checks generated, checks approved "on behalf of the DQ approver", code generated, validated, pull request opened. It stops at "Pull request opened; waiting on the Data Developer's code review" |
| 7 | Data Developer | Home, Action required, then Review | Open the PR link, approve the code | The request reads Done; the requester's history ends with "Every part is done" |

Check along the way: the Data Designer cannot run Build end to end (button off, "needs RUN.OPERATE"); the Forward
Engineering Lead cannot approve the STTM or the code; the Product Owner never has an action after filing.

## Flow B: a source already ready

A source landed in the domain whose run is at "STTM approved" (design already approved).

| # | Persona | Where | Do | Expect to see |
|---|---|---|---|---|
| 1 | GDP Product Owner | Start a workflow, Data source, New | Pick the registered source, Submit | The request links the existing run at once and sits at Build, waiting on the Forward Engineering Lead |
| 2 | Forward Engineering Lead | Build, Selected | Build end to end | As Flow A step 6: stops at code review with the pull request open |
| 3 | Data Developer | Review | Approve the code | The request reads Done |

## Flow C: enhance a source

| # | Persona | Where | Do | Expect to see |
|---|---|---|---|---|
| 1 | GDP Product Owner | Start a workflow, Data source, Enhance | Pick a source already feeding the domain, say what to add, Submit | Steps: Scope, Impact & delta mapping, Regenerate & regression, Review, Done. No administrator step while the source is landed |
| 2 | Data Designer | Intake, Selected | Start enhancement run | A new run of the source, linked to the request; the first onboarding run is not reused |
| 3 | Data Designer, then Forward Engineering Lead, then Data Developer | Design, Build, Review | As Flow A steps 5 to 7 | No "Source onboarded" milestone; "Design stored", then Done |

## Domain requests with files

| # | Persona | Where | Do | Expect to see |
|---|---|---|---|---|
| 1 | GDP Product Owner | Start a workflow, Data domain, New | "Download sample domain JSON", attach it and any other files (Excel, PDF, any format), name the domain, Submit | The request page lists the files, "Kept until the domain exists" |
| 2 | Data OS Administrator | Enable, new domain | Onboard the domain with the requested name | The request links the domain; its files move to the domain's documents ("Files added to the domain's documents" in history) and are read; proposed knowledge appears in the steward's review |
| 3 | GDP Product Owner | Start a workflow, Data domain, Enhance | Pick the domain, say what it needs, attach files | Files go to the domain's documents straight away |

## The PROPERTY pack

After the seed: Model Workbench, PROPERTY shows draft v2 "Pack update from domain/property/domain_pack.json". It
retires PROPERTY_OWNER and carries PROPERTY_CORE, PROPERTY_USAGE and PROPERTY_ADDRESS with their columns. A Data
Designer submits it and a second model approver approves it; the domain page then shows the new tables and the 40
knowledge items.
