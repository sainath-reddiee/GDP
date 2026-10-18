"""The deploy times every statement, so a slow deploy names the statement it is waiting on."""

import time

from infrastructure.deploy import TimedConnection


class Cur:
    def __init__(self, ran):
        self.ran, self.description = ran, [("X",)]

    def execute(self, sql, *a, **kw):
        if "SLOW" in sql:
            time.sleep(0.05)
        self.ran.append(" ".join(sql.split()))
        return self

    def fetchall(self):
        return [(1,)]


class Con:
    def __init__(self):
        self.ran, self.closed = [], False

    def cursor(self):
        return Cur(self.ran)

    def close(self):
        self.closed = True


def test_statements_run_one_by_one_and_slow_ones_are_named():
    con, lines = Con(), []
    timed = TimedConnection(con, lines.append, slow=0.04, beat=3600)
    timed.execute_string("CREATE TABLE A (X INT);\nSELECT 'SLOW' AS S;\nGRANT SELECT ON A TO ROLE R;")
    assert con.ran == ["CREATE TABLE A (X INT);", "SELECT 'SLOW' AS S;", "GRANT SELECT ON A TO ROLE R;"]
    assert timed.statements == 3 and "SLOW" in timed.slowest[1]
    assert len(lines) == 1 and "SELECT 'SLOW'" in lines[0]
    # a cursor keeps working as the connector's does (execute returns the cursor; reads pass through)
    assert timed.cursor().execute("SELECT 1").fetchall() == [(1,)]
    timed.close()
    assert con.closed


def test_a_long_statement_is_reported_while_it_runs():
    con, lines = Con(), []
    timed = TimedConnection(con, lines.append, slow=3600, beat=0.02)
    timed.timed("CALL LONG_ONE()", lambda: time.sleep(0.1))
    timed.close()
    assert any("still running" in line and "CALL LONG_ONE()" in line for line in lines)
