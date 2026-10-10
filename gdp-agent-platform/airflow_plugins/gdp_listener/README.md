# GDP listener plugin for Airflow (MWAA)

The plugin pushes DAG run and task instance changes to the GDP platform within seconds, so failures reach the ops
pages and (later) incidents without waiting for the next poll. It is optional: the platform's poller reads MWAA through
`invoke_rest_api` every few minutes and captures everything on its own. Push only shortens the delay.

It works with Airflow 2.7 and later, and with Airflow 3.x. It uses Airflow listener hooks
(`on_dag_run_running/success/failed`, `on_task_instance_running/success/failed`), sends one small signed JSON event per
change with a 3 second timeout from a background thread, never raises into Airflow, never blocks a task and logs
nothing.

## Contents

```
gdp_listener_plugin.py      registers the listener (AirflowPlugin)
gdp_listener/__init__.py
gdp_listener/core.py        payloads, HMAC signing, the sender (no Airflow import)
gdp_listener/listener.py    the hook implementations
```

## 1. Create the environment and the push secret in GDP

1. Admin, Integrations, Airflow: add the MWAA environment (name and region) and test the connection.
2. Turn on push for the environment and choose "Rotate push secret". The secret is shown once; copy it straight into
   the MWAA configuration below. Rotating again invalidates the old secret immediately.
3. Note the environment id shown in Admin (for example `prod-mwaa`) and the ingest URL: your GDP web address followed
   by `/bff/ops/ingest`, for example `https://gdp.example.com/bff/ops/ingest`.

## 2. Package plugins.zip

The two entries must be at the root of the zip (not inside a parent folder):

```sh
cd airflow_plugins/gdp_listener
zip -r ../../plugins.zip gdp_listener_plugin.py gdp_listener -x "*/__pycache__/*"
```

On Windows PowerShell:

```powershell
cd airflow_plugins\gdp_listener
Compress-Archive -Path gdp_listener_plugin.py, gdp_listener -DestinationPath ..\..\plugins.zip -Force
```

If your environment already has a plugins.zip, add these two entries to it instead of replacing it.

## 3. Install on MWAA

1. Upload `plugins.zip` to the environment's S3 bucket and select the new object version in the MWAA environment
   (Edit, DAG code in Amazon S3, Plugins file).
2. Add these Airflow configuration options (Edit, Airflow configuration options). MWAA exposes them to every Airflow
   process as `AIRFLOW__GDP__*` variables, which the plugin reads:

   | Option             | Value                                         |
   |--------------------|-----------------------------------------------|
   | `gdp.ingest_url`   | `https://<your GDP web address>/bff/ops/ingest` |
   | `gdp.env_id`       | the environment id from Admin                 |
   | `gdp.push_secret`  | the secret from "Rotate push secret"          |

   Outside MWAA you can set `GDP_INGEST_URL`, `GDP_ENV_ID` and `GDP_PUSH_SECRET` as environment variables instead.
   Without all three the plugin does nothing.
3. Save. MWAA restarts the scheduler, workers and web server with the plugin (this takes 20 to 30 minutes).
4. Trigger a DAG and watch its run appear on the Pipelines page within seconds.

Treat the push secret like a password: keep it in the MWAA configuration (or AWS Secrets Manager through MWAA's
secrets backend), never in DAG code or a repository.

## Network

The plugin needs outbound HTTPS from the MWAA workers and scheduler to the GDP web address. MWAA environments in
private network mode usually have no internet egress; there, either allow egress to that one host (NAT gateway or a
proxy) or skip the plugin. The poller alone captures every run either way; push only reduces the delay from the poll
interval to a few seconds.

## Request format

`POST <ingest_url>` with a compact JSON body and these headers:

| Header            | Value                                                              |
|-------------------|--------------------------------------------------------------------|
| `X-GDP-Env`       | environment id                                                     |
| `X-GDP-Timestamp` | unix seconds; requests more than 5 minutes off are rejected        |
| `X-GDP-Event-Id`  | a random id; a repeated id is rejected as a replay                 |
| `X-GDP-Signature` | hex HMAC-SHA256 of `"<timestamp>.<raw body>"` keyed with the secret |

Bodies are at most 64 KB. A failed task's error text is cut to its last 2000 characters; the platform redacts secrets
and personal data before storing it.
