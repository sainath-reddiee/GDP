"""Self-hosted Airflow (KIND 'AIRFLOW'): the REST client and the client factory."""

import json

import pytest

from services.ops.airflow_rest import AirflowRest, credentials, env_key
from services.ops.mwaa import Mwaa, MwaaError, client_for


class FakeAirflow:
    """Airflow 3 at http://af: /auth/token issues tokens; `expire` makes the next call answer 401 once."""

    def __init__(self, v2=True, expire=False):
        self.v2, self.expire, self.calls, self.tokens = v2, expire, [], 0

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers.get("Authorization"), json.loads(body) if body else None))
        if url.endswith("/auth/token"):
            if json.loads(body)["password"] != "pw":
                return 401, {"detail": "bad"}
            self.tokens += 1
            return 201, {"access_token": f"t{self.tokens}"}
        prefix = "/api/v2" if self.v2 else "/api/v1"
        if "/api/v2/" in url and not self.v2:
            return 404, None
        if url.endswith("/version"):
            return 200, {"version": "3.3.2" if self.v2 else "2.10.5"}
        if self.expire and headers.get("Authorization") == "Bearer t1":
            self.expire = False
            return 401, {"detail": "token expired"}
        if "/dags?" in url:
            return 200, {"dags": [{"dag_id": "lab_a"}], "total_entries": 1}
        if url.endswith("/clearTaskInstances"):
            return 200, {"task_instances": [{"task_id": "t"}]}
        if "/missing" in url:
            return 404, {"detail": "DAG not found"}
        if "/boom" in url:
            return 500, {"detail": "kaput"}
        return 200, {"url": url.replace(f"http://af{prefix}", "")}


def test_airflow3_uses_a_token_and_v2_paths():
    fake = FakeAirflow()
    client = AirflowRest("http://af/", "admin", "pw", http=fake)
    assert client.version() == {"version": "3.3.2", "api_version": "v2"}
    dags, total = client.dags()
    assert dags == [{"dag_id": "lab_a"}] and total == 1
    assert any(c[1].startswith("http://af/api/v2/dags?") and c[2] == "Bearer t1" for c in fake.calls)
    client.clear_task_instances("lab_a", ["t"], "r1", dry_run=False)
    method, url, _, body = fake.calls[-1]
    assert method == "POST" and url == "http://af/api/v2/dags/lab_a/clearTaskInstances" and body["dry_run"] is False


def test_an_expired_token_is_fetched_again_once():
    fake = FakeAirflow(expire=True)
    client = AirflowRest("http://af", "admin", "pw", http=fake, sleep=lambda s: None)
    client.dags()
    assert fake.tokens == 2


def test_errors_map_to_the_mwaa_kinds():
    client = AirflowRest("http://af", "admin", "pw", http=FakeAirflow(), sleep=lambda s: None)
    with pytest.raises(MwaaError) as e:
        client.invoke("GET", "/dags/missing")
    assert e.value.kind == "not_found"
    with pytest.raises(MwaaError) as e:
        client.invoke("GET", "/boom")
    assert e.value.kind == "unavailable"
    with pytest.raises(MwaaError) as e:
        AirflowRest("http://af", "admin", "wrong", http=FakeAirflow()).dags()
    assert e.value.kind == "credentials"
    with pytest.raises(MwaaError) as e:
        AirflowRest("http://af", None, None, http=FakeAirflow(), env_id="local-lab").dags()
    assert e.value.kind == "credentials" and "AIP_AIRFLOW_LOCAL_LAB_USER" in str(e.value)


def test_airflow2_uses_basic_auth_and_v1_paths():
    fake = FakeAirflow(v2=False)
    client = AirflowRest("http://af", "admin", "pw", http=fake)
    assert client.api_version == "v1"
    client.dags()
    assert any(c[1].startswith("http://af/api/v1/dags?") and str(c[2]).startswith("Basic ") for c in fake.calls)


def test_client_for_picks_the_kind_and_reads_credentials_from_the_host(monkeypatch):
    assert isinstance(client_for({"kind": "MWAA", "mwaa_env": "prod", "region": "us-east-1"}), Mwaa)
    monkeypatch.setenv("AIP_AIRFLOW_LOCAL_LAB_USER", "admin")
    monkeypatch.setenv("AIP_AIRFLOW_LOCAL_LAB_PASSWORD", "pw")
    c = client_for({"env_id": "local-lab", "kind": "AIRFLOW", "airflow_url": "http://localhost:8080", "mwaa_env": "local",
                    "region": "local"})
    assert isinstance(c, AirflowRest) and c.base == "http://localhost:8080" and (c.username, c.password) == ("admin", "pw")
    assert env_key("local-lab") == "LOCAL_LAB" and credentials("local-lab") == ("admin", "pw")


def test_airflow3_error_detail_becomes_the_traceback_and_full_message():
    from services.ops.mwaa import log_text
    from services.resolve.rules import parse_failure

    text = log_text([
        {"timestamp": "2026-10-16T09:45:37Z", "event": "Running statement: SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN",
         "level": "info"},
        {"timestamp": "2026-10-16T09:45:39Z", "event": "Task failed with exception", "level": "error", "error_detail": [
            {"exc_type": "ProgrammingError", "is_cause": False,
             "exc_value": "000904 (42000): SQL compilation error: error line 1 at position 11\ninvalid identifier 'AMOUNT_USD'",
             "frames": [{"filename": "/dags/lab_dags.py", "lineno": 80, "name": "publish_revenue"}]}]},
        {"timestamp": "2026-10-16T09:45:40Z", "event": "Post Execute", "level": "info"}])
    assert 'File "/dags/lab_dags.py", line 80, in publish_revenue' in text
    parsed = parse_failure(text)
    assert parsed["exception_class"] == "ProgrammingError"
    assert parsed["exception_text"].endswith("invalid identifier 'AMOUNT_USD'")


def test_the_excerpt_keeps_the_last_statement_before_the_traceback():
    from services.ops.normalize import excerpt

    log = ("t INFO Running statement: SELECT 1 FROM CORE.LAB_A\nt INFO Running statement: SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN\n"
           "t ERROR Task failed\nTraceback (most recent call last):\n  File \"x\", line 1, in f\nProgrammingError: invalid identifier 'AMOUNT_USD'")
    out = excerpt(log)
    assert out.splitlines()[0].endswith("FROM CORE.LAB_ORDERS_CLEAN") and "CORE.LAB_A" not in out
    assert out.splitlines()[1].startswith("Traceback")
