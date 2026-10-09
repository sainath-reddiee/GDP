import hashlib

from services.knowledge import writer


class _Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else dict.__getitem__(self, k)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class FakeSession:
    """Answers the writer's queries from canned results; records every statement."""

    def __init__(self, current=None, max_version=0, policy=None):
        self.current, self.max_version, self.policy = current, max_version, policy
        self.sql_log = []

    def sql(self, sql, params=None):
        self.sql_log.append((" ".join(sql.split()), params))
        if "FROM CORE.PLATFORM_CONFIG" in sql:
            return _Result([_Row(CONFIG_VALUE=self.policy)] if self.policy is not None else [])
        if sql.lstrip().startswith("SELECT KNOWLEDGE_ID, CONTENT"):
            return _Result([_Row(self.current)] if self.current else [])
        if "SELECT MAX(VERSION)" in sql:
            return _Result([_Row(V=self.max_version)])
        return _Result([])

    def statements(self, prefix):
        return [s for s, _ in self.sql_log if s.startswith(prefix)]


def test_lineage_matches_the_migration_backfill():
    assert writer.lineage_id("D1", "column.X") == hashlib.md5(b"D1|column.X").hexdigest()
    assert writer.lineage_id(None, "dbt.branch.r1") == hashlib.md5(b"|dbt.branch.r1").hexdigest()


def test_policy_defaults_and_overrides():
    assert writer.mode_for({}, "MAPPING_PATTERN", "MAPPING") == "auto"
    assert writer.mode_for({}, "QA_TEST", "QA") == "review"
    assert writer.mode_for({}, "EXCEPTION", "QUALITY") == "review"
    assert writer.mode_for({"QA_TEST": "auto"}, "QA_TEST", "QA") == "auto"
    assert writer.mode_for({"mapping_pattern": "review"}, "MAPPING_PATTERN", "MAPPING") == "review"
    assert writer.mode_for({"QA_TEST": "review"}, "QA_TEST", "USER") == "auto"  # people's own edits are never queued
    assert writer.mode_for({}, "BUSINESS_RULE", "COPILOT") == "review"
    assert writer.mode_for({"ORIGIN:COPILOT": "auto"}, "BUSINESS_RULE", "COPILOT") == "auto"


def test_identical_content_writes_nothing():
    s = FakeSession(current={"KNOWLEDGE_ID": "k1", "CONTENT": "same", "CONTENT_JSON": '{"a": 1}', "STATUS": "ACTIVE"})
    assert writer.remember(s, domain_id="D", kind="MAPPING_PATTERN", key="feedback.T.S.C", title="t", content="same",
                           content_json={"a": 1}, origin="MAPPING") is None
    assert not s.statements("INSERT") and not s.statements("UPDATE")


def test_auto_supersedes_and_inserts_current_version_with_provenance():
    s = FakeSession(current={"KNOWLEDGE_ID": "k1", "CONTENT": "old", "CONTENT_JSON": None, "STATUS": "ACTIVE"}, max_version=2)
    kid = writer.remember(s, domain_id="D", kind="TRANSFORMATION_RULE", key="transform.T.C", title="t", content="new",
                          content_json={"x": 1}, origin="STTM", run_id="run-9", confidence=0.8)
    assert kid
    upd = s.statements("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE")
    assert upd and "SUPERSEDED" in upd[0]
    insert = next(p for sql, p in s.sql_log if sql.startswith("INSERT INTO KNOWLEDGE.DOMAIN_KNOWLEDGE"))
    assert "ACTIVE" in insert and "3" in insert and "TRUE" in insert and "STTM" in insert and "run-9" in insert
    assert writer.lineage_id("D", "transform.T.C") in insert


def test_review_policy_proposes_without_touching_the_version_in_use():
    s = FakeSession(current={"KNOWLEDGE_ID": "k1", "CONTENT": "old", "CONTENT_JSON": None, "STATUS": "ACTIVE"}, max_version=1)
    writer.remember(s, domain_id="D", kind="QA_TEST", key="qa.test.1", title="t", content="new", origin="QA", by_domain=False)
    assert not s.statements("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE")
    assert s.statements("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'SUPERSEDED'")  # an older proposal is replaced
    insert = next(p for sql, p in s.sql_log if sql.startswith("INSERT INTO KNOWLEDGE.DOMAIN_KNOWLEDGE"))
    assert "PROPOSED" in insert and "FALSE" in insert


def test_explicit_status_bypasses_policy():
    s = FakeSession(policy={"SODA_PATTERN": "review"})
    writer.remember(s, domain_id="D", kind="SODA_PATTERN", key="SODA.T.C.X", title="t", content="rejected", origin="SODA",
                    status="DRAFT")
    insert = next(p for sql, p in s.sql_log if sql.startswith("INSERT INTO KNOWLEDGE.DOMAIN_KNOWLEDGE"))
    assert "DRAFT" in insert and "TRUE" in insert


def test_usage_never_fails_the_stage():
    class Broken:
        def sql(self, *a, **k):
            raise RuntimeError("no table")

    writer.record_usage(Broken(), "r", "MAPPING", ["k1"])  # swallowed
    s = FakeSession()
    writer.record_usage(s, "r", "MAPPING", ["k1", "k1", ""])
    assert len(s.statements("INSERT INTO KNOWLEDGE.KNOWLEDGE_USAGE")) == 1
    writer.record_usage(s, "r", "MAPPING", [])
    assert len(s.statements("INSERT INTO KNOWLEDGE.KNOWLEDGE_USAGE")) == 1
