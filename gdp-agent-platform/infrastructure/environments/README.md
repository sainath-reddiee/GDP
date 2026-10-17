# Deploying to a client Snowflake account

One command deploys everything, in order, as the role you were given. No SYSADMIN, no ACCOUNTADMIN, and no
SECURITYADMIN for the deploy itself.

```
python infrastructure/deploy.py --env client-prod
```

## Who does what

## Access model: AODS account roles (default)

There are no database roles. The platform uses ten account roles, created once by the role administrator:

| Account role | Who gets it |
|---|---|
| `AODS_PLATFORM_ADMIN` | The deploy role and platform administrators |
| `AODS_DATA_ENGINEER` | Data engineers who run onboarding |
| `AODS_QA_ENGINEER` | QA engineers |
| `AODS_DATA_STEWARD`, `AODS_DOMAIN_OWNER`, `AODS_REVIEWER` | People who review and approve |
| `AODS_VIEWER` | Read-only users |
| `AODS_OPS_SERVICE` | The ops worker's service user |
| `AODS_OPS_VIEWER` | Operations dashboards, read only |
| `AODS_SERVICE_AGENT` | The Cortex agent's service use |

`--admin-sql` writes the `CREATE ROLE` statements and the 15 links between the roles (for example `AODS_VIEWER`
inside `AODS_DATA_ENGINEER`), so a person gets everything below their role.

The deploy grants every table, view and procedure privilege straight to these roles. An object's owner may grant it
to any role, so the deploy needs no MANAGE GRANTS. The platform schemas (`CORE`, `LANDING`, `GDP_SILVER`) are
**managed access** schemas. That lets the deploy role, as their owner, define future grants, and it means only the
deploy role decides who gets what in them.

Future grants are deliberately narrow. `AODS_PLATFORM_ADMIN` gets all privileges on future tables, views,
procedures, functions, stages, sequences and file formats. `AODS_VIEWER` gets SELECT on future views. Tables stay
granted one by one, because some hold secrets, such as integration settings and the Airflow push secret.

Use `role_prefix = "AODS_PROD"` (giving `AODS_PROD_DATA_ENGINEER` and so on) when two environments share one
Snowflake account, so a role never reaches the other environment's database.

## Who does what

| Who | What | How often |
|---|---|---|
| Your role administrator (your leader's admin role) | Creates the ten `AODS_*` roles, links them, gives them to people | Once |
| SYSADMIN, or whoever can create databases | Creates the platform database and hands you ownership (or grants your role CREATE DATABASE) | Once |
| The warehouse owner | Gives the `AODS_*` roles USAGE on the warehouse (unless your role owns it) | Once |
| ACCOUNTADMIN | Cortex access; optional EXECUTE TASK, GitHub integration, usage views | Once |
| You (`AODS_PLATFORM_ADMIN`) | Everything else: schemas, tables, procedures, search services, agents, all grants, future grants | Every release |

Nobody creates database roles. Your leader's admin role only needs to create account roles, which it can.

The older model, with database roles inside the platform database granted to account roles you list, is still
available with `model = "database_roles"`. The DEV environment uses it.

## First time

1. Install the Python requirements (`pip install -r apps/api/requirements.txt`) on the machine that deploys.
2. Create the environment file by answering a few questions: a saved connection or the account identifier, your
   user, browser SSO or a key file, the role, warehouse and database. It is written to `client-prod.toml`, which is
   not committed, and it never holds a password. Running any command for an environment with no file starts the same
   questions.
   ```
   python infrastructure/deploy.py --env client-prod --setup
   ```
3. Check the privileges:
   ```
   python infrastructure/deploy.py --env client-prod --check
   ```
4. If anything required is missing, write the admin SQL and send it to your admins:
   ```
   python infrastructure/deploy.py --env client-prod --admin-sql
   ```
   The file (`deploy-logs/client-prod-admin.sql`) holds only the statements this environment still needs, grouped
   under the role that must run each block. It never contains database roles.
5. When `--check` shows no required item missing, deploy:
   ```
   python infrastructure/deploy.py --env client-prod
   ```

## Every release

```
python infrastructure/deploy.py --env client-prod --plan    # what will change, read only
python infrastructure/deploy.py --env client-prod           # deploy (asks you to type the database name)
```

Use `--yes` to skip the prompt in a pipeline. Each run writes a log to `deploy-logs/`, which you can attach to the
client's change record.

## What runs, in order

1. **Preflight** checks the role, warehouse, database ownership, Cortex, the `[access]` roles and the optional
   features. It stops before changing anything when a required privilege is missing.
2. **Plan** lists the pending migrations and the steps whose inputs changed. It stops if an applied migration was
   edited.
3. **Deploy** runs the following steps in dependency order. Each step is skipped when nothing in it changed, so a
   repeat deploy takes seconds.
   1. Database (only if it does not exist yet) and the migration ledger.
   2. Schema and tables, from the baseline on a new database, then the migrations. These also create the database
      roles and their grants.
   3. The workflow seed.
   4. The code package, uploaded to the stage.
   5. The stored procedures.
   6. The GitHub publisher and Oracle source refreshes, when those are set up.
   7. The knowledge seed: domain packs, skills and platform config.
   8. Views.
   9. Cortex Search services.
   10. Agents.
4. **Access** grants each platform database role to the account roles in `[access]`. It also grants warehouse usage
   when your role owns the warehouse. Nothing is ever revoked.
5. **Verify** checks that the migrations are current and that the procedures, search services, agent and all ten
   database roles exist.

A failed deploy is safe to run again. Finished steps are skipped and the failed step is retried.

## Required privileges for the deploy role

- USAGE on the warehouse.
- Ownership of the platform database, or CREATE DATABASE on the account so the deploy creates it and owns it.
- `SNOWFLAKE.CORTEX_USER`. Most accounts grant it to PUBLIC; `--check` tells you.

Optional, for features that stay off without them:

- EXECUTE TASK, for the 30-minute pull request sync.
- USAGE on a GitHub external access integration, for publishing pull requests.
- `SNOWFLAKE.USAGE_VIEWER`, for actual AI credits instead of estimates.
