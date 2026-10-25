# Plan: Build Workflows, Validate & Publish UX Stitching, and Client SQL Migration

## Overview
1. **Stage & Terminology Unification (`Initiate → Design → Build → Validate → Publish`)**:
   - Rename `Platform actions` to **`Build Workflows`** on Home (`/os`).
   - Rename `Intake` and `Enable` to **`Initiate`**, `Review` to **`Validate`**, and `Release` / `Done` to **`Publish`** across workflow tracks (`lib/os/requests.ts`), run lifecycle steps (`lib/os/lifecycle.ts`), flow strips (`/os/build/ready`), and navigation labels (`lib/os/access.ts`).
2. **Home Screen (`/os`) & Workflow Card (`WorkflowRow`) Simplification**:
   - Remove standalone sub-task rows (`PlatformActionRow` / `loadActions`) and the `Platform` filter tab from Home (`/os/page.tsx`) so Home exclusively displays main **Build Workflows** with accurate counts.
   - Declutter `WorkflowRow` (`components/os/home.tsx`) by removing:
     - `· filed <time> ago`
     - `High priority` tag
     - `Your turn` tag
     - Red sub-task attention line (`Validation failed on ...`)
     - `Next for you: ...` line
     - `The source` expandable `<details>` block
   - Ensure workflow titles and grouped status badges present a clean, executive view of what is being done.
3. **Stitching `Build` (`/os/build`), `Validate` (`/os/review`), and `Publish` (`/os/release`) with Filters & Workflow Grouping**:
   - Add `BoardTabs` (`Selected | Action required | Active | Completed`), domain filter pills, status/gate filter pills, and search to **Validate** (`/os/review`) and **Publish** (`/os/release`), plus domain filtering and workflow grouping on **Build** (`/os/build`).
   - Group multi-table child runs under their parent **Workflow Card** (with interactive target-table pills and workflow summary) instead of listing flat technical run rows.
   - Add a unified **5-Stage Workflow Journey Bar (`01 Initiate → 02 Design → 03 Build → 04 Validate → 05 Publish`)** and **Target Tables Switcher** at the top of the selected workspace on Build, Validate, and Publish for 1-click navigation between stages and tables.
4. **Capture SQL Fixes for Client & Dev Deployments (`V081` + Domain Packs)**:
   - Update spoke audit column datatypes (`GDP_IS_ACTIVE` → `BOOLEAN`, `GDP_INSERTED_TS` / `GDP_UPDATED_TS` → `TIMESTAMP_NTZ`) in `domain/*/domain_pack.json`.
   - Create `snowflake/database/migrations/V081__audit_column_types_and_domain_members.sql` (and update `snowflake/database/baseline/B061__platform_core.sql`) to clean placeholder domain members (`'GDP DATA OFFICE'`, etc.) and normalize audit column datatypes across `TARGET_COLUMN_REGISTRY`, `STTM_LINE`, and `SODA_EXPECTATION_REGISTRY` on every environment deploy.
   - Run unit tests, TypeScript checks, deploy to Snowflake (`dev` and `client-prod`), and verify live screens.