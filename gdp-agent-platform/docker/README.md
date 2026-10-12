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
