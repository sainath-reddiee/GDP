# Docker deployment

Three containers from two images: `web` (Next.js), `api` (FastAPI) and `worker` (same image as the API, runs
`python -m app.worker`). Only `web` is published, on the host loopback, for an HTTPS reverse proxy. The API stays on
the compose network. Run one `api` replica: signed-in sessions live in its memory.

```bash
cp docker/.env.example docker/.env            # release tag, build SHA, AIP_AUTH
cp docker/api.env.example docker/api.env      # Snowflake account, database, secrets
mkdir -p docker/secrets && cp <path>/aip_service_key.p8 docker/secrets/aip_service_key.p8
docker compose -f docker/docker-compose.yml --env-file docker/.env up -d --build
docker compose -f docker/docker-compose.yml --env-file docker/.env ps
```

Deploy the Snowflake objects from a workstation with `python infrastructure/deploy_snowflake.py` (browser SSO), or
from the API image with a key-pair connection mounted at `/home/gdp/.snowflake/connections.toml`:

```bash
docker compose -f docker/docker-compose.yml --env-file docker/.env run --rm --no-deps \
  -v ~/.snowflake:/home/gdp/.snowflake:ro api \
  python /srv/gdp/infrastructure/deploy_snowflake.py --connection <name> --database <DB> --warehouse <WH>
```

`AIP_AUTH` is a build argument of the web image as well as a runtime variable, because the sign-in middleware is
compiled into the build. Rebuild the web image after changing it.

## HTTPS reverse proxy

Put a reverse proxy on the public host name in front of `127.0.0.1:3000`. `docker/nginx.conf.example` is a working
nginx configuration. Whatever the proxy (nginx, an AWS ALB, Azure Application Gateway, IIS), it needs:

- request bodies up to 200 MB (source files, Copilot attachments, imports);
- for `/bff/copilot/*`: response buffering off and an idle or read timeout of at least 15 minutes, because the Copilot
  streams each turn and each publish as server-sent events and a turn can run several minutes (the app already sends
  `X-Accel-Buffering: no` and `Cache-Control: no-transform`);
- HTTPS end to end for people: the image runs in production mode, where the sign-in cookie is marked secure, so
  signing in over plain HTTP does not work.

## Before the first sign-in

1. Deploy the Snowflake objects (above): migrations up to V050, including the Copilot workspace (V048, V049) and the
   Tickets page's saved views (V050).
2. In Admin, AI models, pick a model for the COPILOT stage that Cortex offers in the client's region.
3. Jira (if in scope): the Atlassian OAuth app needs the scopes `read:jira-work`, `write:jira-work`, `read:jira-user`,
   `offline_access`, `read:board-scope:jira-software`, `read:sprint:jira-software` and `read:project:jira`; set
   `JIRA_REDIRECT_URI` to `https://<web host>/bff/jira/callback`. Tickets and the Copilot create issues and set sprints
   with these scopes (the sprint is set through the issue's Sprint field), so no further Jira consent is needed.
4. Give people the app roles they need: AI.USE for the Copilot, JIRA.READ or CASE.WORK for Tickets, JIRA.WRITE to
   create tickets and move them to sprints.
