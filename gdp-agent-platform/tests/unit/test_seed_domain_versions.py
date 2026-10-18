"""The deploy records a domain version for each domain. Its connection has no current database, so every table the
snapshot touches must be qualified with the database (an unqualified CORE.* failed with "no current database")."""

import re

from infrastructure.seed_knowledge import snapshot_domains


class Cur:
    def __init__(self):
        self.sql, self.description, self._rows = [], [], []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if "FROM DEV.CORE.DOMAIN_REGISTRY" in sql and "DOMAIN_ID, DOMAIN_NAME" in sql:
            self._rows, self.description = [("d1", "COMPANY")], [("DOMAIN_ID",), ("DOMAIN_NAME",)]
        else:
            self._rows, self.description = [], [("X",)]

    def fetchall(self):
        return self._rows


def test_every_snapshot_query_names_the_database():
    cur = Cur()
    log = snapshot_domains(cur, "DEV")
    assert not any("no current database" in line for line in log)
    statements = cur.sql[1:]
    assert statements, "the snapshot ran no SQL"
    for sql in statements:
        assert not re.search(r"(?<![\w.\"])CORE\.", sql), sql
        assert "DEV.DEV." not in sql
