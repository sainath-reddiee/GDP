# Operations in a client environment: Amazon MWAA and Datadog

Operations reads the client's real Airflow (Amazon MWAA) for DAGs, runs, task grids and task logs, and Datadog for
pipeline logs and monitors. No credential is ever stored in the platform: AWS access comes from the host's role, and
the Datadog keys come from the host's environment. This guide sets up both and proves the connection before anything
is added in the app.

## 1. Amazon MWAA

### What the API host needs

The API and the worker call MWAA with `boto3` through the default AWS credential chain. In order of preference:

1. **A role on the host.** An EC2 instance profile (for containers, set the instance metadata hop limit to 2) or an
   ECS task role.
2. **A named profile** (`AWS_PROFILE`), for a workstation test with `aws sso login`.
3. **An access key of a dedicated IAM user** in `docker/api.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`),
   only when no role can be attached. Never a person's key; rotate it.

Set `AWS_REGION` in `docker/api.env` in every case. Temporary keys (an `ASIA...` key with `AWS_SESSION_TOKEN`, as
the AWS access portal hands out) work for a one-off check, but they expire within hours, so the worker needs a role or a
refreshing profile. Keys never go into a script or a file in the repository.

### The IAM policy

The service account (the role or user above) needs:

| Action | Resource | Why |
|---|---|---|
| `airflow:GetEnvironment` | `arn:aws:airflow:<region>:<account>:environment/<env>` | Environment status and version |
| `airflow:InvokeRestApi` | `arn:aws:airflow:<region>:<account>:role/<env>/<Airflow role>` | The Airflow REST API (DAGs, runs, logs) |
| `logs:FilterLogEvents`, `logs:GetLogEvents` | the environment's task log group | Task logs from CloudWatch |

The Airflow role in the `InvokeRestApi` resource is the Airflow RBAC role the calls run as: `User` is enough to
read, `Op` is needed to clear (retry) tasks from an incident. Use `Viewer` only if retries from the platform are not
wanted. "Already has access" in the AWS console (for example `CreateWebLoginToken` for the Airflow UI) is not the same
permission: the platform needs `InvokeRestApi`.

### Prove it from the host

From `gdp-agent-platform/` on the API host (or a workstation with the same identity):

```
python infrastructure/ops_check.py --mwaa-env <MWAA environment name> --region <region>
```

It prints the AWS identity, the environment status and Airflow version, the REST API version, how many DAGs are
visible with the first ten, and the latest runs of one DAG (`--dag <id>` to choose it). Each FAIL line says which
permission is missing. Nothing is written anywhere.

### Add it in the app

1. **Admin, Integrations, Airflow, Add environment.** Kind MWAA, the environment name and the region.
2. **Test connection.** You should see the Airflow version and sample DAGs.
3. **Refresh the task catalogue,** then **Poll now**. DAGs and runs appear on the Pipelines and Operations pages,
   and failures open incidents from then on (the worker polls every environment on its interval).

## 2. Datadog

### Keys

Put these in `docker/api.env` (the API and the worker share it; `apps/api/.env` when the API runs locally), then
restart the API and the worker:

```
DATADOG_SITE=<your site: datadoghq.com, us3.datadoghq.com, us5.datadoghq.com, datadoghq.eu, ap1.datadoghq.com>
DATADOG_API_KEY=<API key>
DATADOG_APP_KEY=<application key>
DATADOG_APP_URL=<optional: your custom Datadog web address, for links>
```

The application key needs only the scopes `logs_read_data` and `monitors_read`; create a scoped key for the
platform. Only Datadog's own hosts (`api.<site>`) ever receive the keys, and the app never shows them, even to admins.

### Prove it from the host

```
python infrastructure/ops_check.py --datadog
```

It checks the API key, a log search and the monitor list, and says which scope is missing when one fails. Both checks
can run together: `--mwaa-env <env> --region <region> --datadog`.

### Switch it on

**Admin, Integrations, Datadog.**

1. The keys step shows the site, and whether each key is set.
2. **Test connection.**
3. Set what Operations does with Datadog, then **Save** with **Use Datadog** on:
   - **Log search for an incident.** Datadog log search syntax, using the tags your pipelines send. The default,
     `dag_id:{dag_id} task_id:{task_id}`, fits the Datadog Airflow integration. `{dag_id}`, `{task_id}`, `{run_id}`,
     `{env_id}` and `{try_number}` are filled in from the incident, and a term whose value is missing is left out.
     Add fixed terms as needed, for example `service:airflow env:prod`.
   - **Window and size.** Minutes before the failure, and the most log lines to read.
   - **When a task fails, read its error from.** Airflow, then Datadog when Airflow's log cannot be read (the
     default), or Datadog first. The error is stored with the task run and takes the usual path, described below.
   - **Give the AI diagnosis Datadog's logs.** Used when the Airflow task log cannot be read (expired, or no AWS access).
   - **Alerting monitors open incidents.**
     - Choose the monitor tags to watch (for example `team:data`) and the states that count: Alert, Warn, No Data.
     - Incidents resolve themselves when the monitor recovers.
     - A monitor's `dag_id:` tag puts its incident with that pipeline.
     - Severity comes from the monitor's priority, else the default you set.
     - Notifications go to Jira and in-app only.

### What happens when a DAG fails

1. The worker polls MWAA. A failed task run is stored, and its error is read from the task log: Airflow's own, or
   Datadog's for that DAG, task, run and try, as set above.
2. The failure opens an incident (or adds an occurrence) and a failure record for the resolution engine.
3. The resolution engine parses the error (exception class, text, query id, dbt node) and searches the knowledge base
   of known issues.
   - **Known issue:** its fix runs automatically, or waits for a person's approval when the fix requires one. The
     task is then cleared in Airflow.
   - **Not known:** the root-cause analysis runs on the full context (log, code, dbt lineage, recent runs and
     commits, similar incidents), and a Jira ticket is opened with it.
4. Recurring known issues later get a permanent fix proposal, and resolved unknown issues can be published as new
   known fixes, so the next failure of that kind is fixed automatically.

### Where it shows

- **Incident page:** a Datadog logs panel. It shows the search, a window picker, the log lines (redacted), and
  **Open in Datadog** for the same search.
- **AI diagnosis:** cites Datadog's logs when Airflow's own log was not readable.
- **Incidents inbox:** monitors in the chosen states appear as incidents, routed, escalated and ticketed like Airflow
  failures.
- **Check monitors now** (Admin, Datadog) reads the monitors immediately. The worker does it every two minutes.

## When something fails

| Symptom | Cause | Fix |
|---|---|---|
| `AWS credentials are not available on the API host` | No role, profile or key on the host | Attach the role (section 1) |
| `AccessDeniedException` on InvokeRestApi | The resource names a different Airflow role, or the action is missing | Fix the policy's `role/<env>/<Airflow role>` resource |
| `ResourceNotFoundException` | Wrong environment name or region | Check the names in the MWAA console |
| Task logs empty, AI says "stored excerpt" | Logs expired from CloudWatch, or task logging off | Turn on task logs in MWAA, or switch on Datadog logs |
| Datadog: `refused the API key` | Wrong key, or a key from another site | Match `DATADOG_SITE` to the organisation's site |
| Datadog: log search refused | The application key lacks `logs_read_data` | Add the scope, or create a scoped key |
| Datadog logs panel empty | The search finds nothing in the window | Widen the window; check the tag names in the log search |
| Datadog tab says "setup needed" after editing `api.env` | The API was not restarted | Restart the API and the worker |
