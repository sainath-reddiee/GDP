# Deploying to a client Snowflake account

One command deploys everything, in order, as the role you were given. No SYSADMIN, no ACCOUNTADMIN, and no
SECURITYADMIN for the deploy itself.

```
python infrastructure/deploy.py --env client-prod
```

## Who does what

| Who | What | How often |
|---|---|---|
| Your role administrator (your leader's admin role) | Creates the account roles in `[access]` and gives them to people | Once |
| SYSADMIN, or whoever can create databases | Creates the platform database and hands you ownership (or grants your role CREATE DATABASE) | Once |
| ACCOUNTADMIN | Cortex access; optional EXECUTE TASK, GitHub integration, usage views | Once |
| You (platform admin role) | Everything else: schema, tables, **database roles**, procedures, search services, agents, access grants | Every release |

Database roles (`<db>.DATA_ENGINEER`, `<db>.VIEWER` and the rest) need no admin: the deploy creates them as the owner
of the database, and grants them to the account roles listed in `[access]`. That is why your leader's admin role
does not need the right to create database roles.

## First time

1. Install the Python requirements (`pip install -r apps/api/requirements.txt`) on the machine that deploys.
2. Copy `example.toml` to `client-prod.toml` and fill it in. It is not committed; never put a password in it.
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
