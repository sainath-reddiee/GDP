# Deploying the platform in a client account, end to end

This is the full sequence that leaves a working app: Snowflake objects, the service user, the three containers, and
the first settings inside the app. Each phase ends with a check, so a problem shows up where it starts.

The example uses the AODS account roles (no database roles), database `AI_PLATFORM`, warehouse `AI_PLATFORM_WH`
and environment file `client-prod`. Replace the names with the client's.

## Phase 0: agree with the client admins (once)

| Item | Who | Notes |
|---|---|---|
| The ten `AODS_*` account roles and their links | Role administrator | `--admin-sql` writes the exact SQL (phase 2) |
| The platform database, owned by `AODS_PLATFORM_ADMIN` | SYSADMIN or any role with CREATE DATABASE | Or grant CREATE DATABASE to `AODS_PLATFORM_ADMIN` |
| A warehouse, usable by the `AODS_*` roles | Warehouse owner | XSMALL with auto-suspend 60 s is enough to start |
| Cortex for `AODS_PLATFORM_ADMIN`, the service role and the users' roles | ACCOUNTADMIN | `SNOWFLAKE.CORTEX_USER`, often already granted to PUBLIC |
| Cross-region inference, if the model is not offered in-region | ACCOUNTADMIN | `--check` tells you |
| A service user for the worker, with a key pair | User administrator | Phase 4 |
| Programmatic access tokens (PATs) for the people who sign in | Snowflake admin | Snowflake requires a network policy or an authentication policy that allows PATs: confirm the client's policy |
| A host for the containers, HTTPS in front | Client infrastructure | Docker; see `docker/README.md` for the proxy settings |
| If the GDP domain packs are used: the silver database name | Data team | The packs name `DEV_GDP_SILVER_DB` (`domain/*/domain_pack.json`) |

## Phase 1: the deploy machine

A workstation (or the API image) with the repository at the release you are deploying, and Python with the API's
requirements:

```
pip install -r apps/api/requirements.txt
```

## Phase 2: environment file and one-time admin SQL

```
python infrastructure/deploy.py --env client-prod --setup
```

Answer: the client's account (or a saved connection), your user, browser SSO or a key file, access model **1
(account roles)**, prefix **AODS**, deploy role **AODS_PLATFORM_ADMIN**, the warehouse and the database. The file
is written to `infrastructure/environments/client-prod.toml`. It is not committed and it never holds a password.

```
python infrastructure/deploy.py --env client-prod --admin-sql
```

Send `deploy-logs/client-prod-admin.sql` to the admins. Each block names the role that runs it: the role
administrator (the ten roles, their links, `AODS_PLATFORM_ADMIN` for you), SYSADMIN (the database), the warehouse
owner, and ACCOUNTADMIN (Cortex, optional items). It never contains database roles.

To show the client exactly what will be created, as plain SQL with their names (nothing runs, no sign-in):

```
python infrastructure/deploy.py --env client-prod --render
```

**Check:** `python infrastructure/deploy.py --env client-prod --check` shows no MISSING line without "(optional)".

## Phase 3: deploy the Snowflake objects

```
python infrastructure/deploy.py --env client-prod --plan
python infrastructure/deploy.py --env client-prod
```

Type the database name to confirm. The deploy runs every step in order:
1. database and ledger;
2. schemas (managed access), tables and policies, then the migrations;
3. workflow seed;
4. code package;
5. procedures;
6. the knowledge seed (domain packs, including GDP's, skills and platform config);
7. views;
8. Cortex Search services;
9. agent;
10. all grants to the `AODS_*` roles and the future grants.

It writes a log to `deploy-logs/`; keep it for the change record.

**Check:** the verify lines at the end say the migrations are current, the procedures, search services and the agent
exist, all ten account roles exist, and CORE is a managed access schema.

## Phase 4: the worker's service user

The worker (failure detection, automatic fixes, permanent fix drafts, the skill scout) signs in as a service user
with a key pair. On the deploy machine:

```
openssl genrsa 2048 | openssl pkcs8 -topk8 -v2 aes256 -out aip_service_key.p8
openssl rsa -in aip_service_key.p8 -pubout -out aip_service_key.pub
```

The user administrator runs, with the public key's text between the BEGIN and END lines:

```sql
CREATE USER IF NOT EXISTS AIP_SERVICE TYPE = SERVICE DEFAULT_ROLE = AODS_OPS_SERVICE
  DEFAULT_WAREHOUSE = AI_PLATFORM_WH RSA_PUBLIC_KEY = '<public key>';
GRANT ROLE AODS_OPS_SERVICE TO USER AIP_SERVICE;
```

The service role also needs warehouse usage and Cortex, from phase 0.

**Check:** `snow connection test` with a key-pair connection for AIP_SERVICE, or a test query in Snowsight run as
`AODS_OPS_SERVICE`, succeeds.

## Phase 5: run the app (three containers)

On the client's host, from `gdp-agent-platform/`:

```
cp docker/.env.example docker/.env
cp docker/api.env.example docker/api.env
mkdir -p docker/secrets && cp aip_service_key.p8 docker/secrets/aip_service_key.p8
```

Fill `docker/api.env`:

```
AIP_AUTH=pat
AIP_ACCOUNT=<client account identifier>
AIP_DATABASE=AI_PLATFORM
AIP_WAREHOUSE=AI_PLATFORM_WH
AIP_SUPER_ADMINS=<your Snowflake user>
AIP_SECRET_KEY=<32 or more random characters>
AIP_SERVICE_USER=AIP_SERVICE
AIP_SERVICE_KEY_PASSPHRASE=<the key's passphrase>
AIP_SERVICE_ROLE=AODS_OPS_SERVICE
AIP_SERVICE_WAREHOUSE=AI_PLATFORM_WH
```

Leave the Jira lines empty unless Jira is in scope. Then:

```
docker compose -f docker/docker-compose.yml --env-file docker/.env up -d --build
docker compose -f docker/docker-compose.yml --env-file docker/.env ps
```

Put the HTTPS reverse proxy in front of `127.0.0.1:3000` (`docker/nginx.conf.example`: 200 MB bodies, no buffering
and a 15-minute timeout for `/bff/copilot/*`).

**Check:** all three containers are up, `docker compose logs worker` shows `ops worker ... started` with no sign-in
error, and the web host opens the sign-in page over HTTPS.

## Phase 6: first sign-in and settings in the app

People sign in with their Snowflake user and a PAT. The role the app uses is the user's default role. Set each
person's default role to their `AODS_*` role, or create the PAT restricted to that role. The first person to sign in
(and everyone in `AIP_SUPER_ADMINS`) becomes the app's super admin.

As super admin:
1. **Governance:** give people their app roles. App roles are separate from Snowflake roles: AI.USE for AI features,
   OPS.VIEW and OPS.OPERATE for Operations, OPS.FIX_PUBLISH for known and permanent fixes, DBT.EDIT for pull requests,
   SKILL.EDIT for skills, AUDIT.VIEW for the Value page, CONFIG.EDIT for settings.
2. **Admin, AI models:** pick models Cortex offers in the client's region, including the COPILOT stage.
3. **Admin, cost:** set the credit price, so AI cost shows in money.
4. **Value page:** keep the effort baselines as assumptions until the delivery team confirms them; money stays hidden
   until then.
5. **Operations:** add the Airflow environment(s), then refresh the task catalogue.
6. **Code:** connect the dbt repository; for pull requests, set up GitHub publishing (needs the integration from
   phase 0).
7. **Jira**, if in scope: see `docker/README.md`.

## Phase 7: smoke test (15 minutes)

| Check | Where | Expect |
|---|---|---|
| Sign in as an engineer | Web | My Work loads, no permission errors |
| Knowledge | Knowledge page | The domain packs and skills are listed |
| AI | Copilot: ask a question about a domain | An answer within a minute (proves Cortex, model and region) |
| Onboarding | Start a run on a small source | The run moves through profiling |
| Operations | Operations page | The Airflow environment polls without error |
| Worker | `docker compose logs worker` | Regular job lines, no repeated failures |
| Value | Value page (AUDIT.VIEW) | Estimated hours, no money until confirmed |

## Every release after this

```
git pull                                                     # the release tag
python infrastructure/deploy.py --env client-prod --plan
python infrastructure/deploy.py --env client-prod
docker compose -f docker/docker-compose.yml --env-file docker/.env up -d --build
```

Deploy the Snowflake objects first, then the containers: new code may need the new tables.

## When something fails

| Symptom | Cause | Fix |
|---|---|---|
| `cannot use role AODS_PLATFORM_ADMIN` | The role is missing or not granted to you | Run the role block of `--admin-sql` |
| `Cortex functions are not available` | No `SNOWFLAKE.CORTEX_USER` | ACCOUNTADMIN grants it (phase 0) |
| `<model> could not be called` | Model not in the region | Cross-region inference, or another model in Admin |
| Deploy stopped half way | Any error | Fix it and run the same command again: finished steps are skipped |
| Worker: `JWT token is invalid` | Wrong key or public key not set | Check phase 4 |
| Sign-in fails with a PAT | No network or authentication policy for PATs | Snowflake admin, phase 0 |
| Pages show "not allowed" | Missing app role | Governance page, phase 6 |
