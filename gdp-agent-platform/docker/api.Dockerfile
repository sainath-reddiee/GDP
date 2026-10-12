# API and ops worker image. One image, two commands: uvicorn for the API, `python -m app.worker` for the worker.
# The API imports the top-level services/ and infrastructure/ packages and reads snowflake/ and domain/ for the
# in-app deploy (Admin, Deploy) and seeding, so those folders ship with it. Build context: gdp-agent-platform/.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/gdp

COPY apps/api/requirements.txt apps/api/requirements.txt
RUN pip install -r apps/api/requirements.txt

COPY pyproject.toml ./
COPY services services
COPY infrastructure infrastructure
COPY snowflake snowflake
COPY domain domain
COPY apps/api apps/api

# the commit this image was built from, reported by /api/health and compared with the web at /bff/version
ARG AIP_BUILD_SHA=""
ENV AIP_BUILD_SHA=${AIP_BUILD_SHA}

# non-root, with a home for the Snowflake connector's cache and ~/.snowflake/connections.toml (deploy only)
RUN useradd --create-home --uid 10001 gdp
USER gdp
WORKDIR /srv/gdp/apps/api

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
