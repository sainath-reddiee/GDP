# API test suite

| File | Layer | Covers |
|---|---|---|
| `test_api_contract_offline.py` | offline (TestClient, fake DB, no Snowflake) | every router mounted, no duplicate routes, `/api` prefix, route ordering, OpenAPI, health, 404/405, malformed JSON / empty / wrong-type bodies on ~50 write endpoints never 5xx, validation error shape, dev-mode login, ingest signature, CORS, headers |
| `test_api_live_read.py` | live (running API) | ~130 collection GETs return JSON, latency, ~90 detail GETs using ids discovered from lists, 404 for missing ids, auth (pat session), role header, knowledge filters/search, lineage, pipelines |
| `test_api_live_writes.py` | live + writes | create/read/update/retire lifecycles: knowledge, ticket views, copilot sessions, ops teams, skill categories, runs, onboarding requests, knowledge policy round trip |

Endpoint lists live in `endpoints.py`.

```powershell
pip install -r apps/api/requirements.txt -r tests/ui/requirements.txt
pytest tests/api -m offline            # no Snowflake needed
pytest tests/api -m live               # needs API at AIP_API_URL (default http://127.0.0.1:8001)
$env:AIP_API_ALLOW_WRITES="1"; pytest tests/api   # include data-changing tests (DEV only)
```

| Variable | Purpose |
|---|---|
| `AIP_API_URL` | live API base URL |
| `AIP_API_SESSION` | `X-AIP-Session` value when the API runs with `AIP_AUTH=pat` |
| `AIP_API_ROLE` | optional `X-AIP-Role` |
| `AIP_API_ALLOW_WRITES` | `1` enables write lifecycle tests |

Live tests are skipped automatically when the API is unreachable.
