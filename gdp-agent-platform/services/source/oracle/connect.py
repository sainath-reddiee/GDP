"""Connections to Oracle with python-oracledb: thin mode by default (no Instant Client), thick mode for wallets
that need it, and retry with backoff on transient network errors."""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, TypeVar

HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
SERVICE = re.compile(r"^[A-Za-z0-9_.$#\-]{1,128}$")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
# Socket drops, listener down, timeouts, broken connections: worth retrying. Anything else (bad password,
# missing table, privilege) fails at once.
TRANSIENT = ("ORA-03113", "ORA-03114", "ORA-03135", "ORA-12541", "ORA-12170", "ORA-12514", "ORA-12537",
             "ORA-12547", "ORA-12571", "DPY-4011", "DPY-6005", "DPI-1080")
ARRAY_SIZE = 10_000
T = TypeVar("T")


@dataclass
class OracleConfig:
    host: str
    port: int = 1521
    service_name: Optional[str] = None
    sid: Optional[str] = None
    user: str = ""
    schema_owner: str = ""
    wallet_dir: Optional[str] = None
    wallet_password: Optional[str] = field(default=None, repr=False)
    connect_timeout: int = 20

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "OracleConfig":
        out = cls(host=str(cfg.get("host") or "").strip(), port=int(cfg.get("port") or 1521),
                  service_name=(str(cfg["service_name"]).strip() if cfg.get("service_name") else None),
                  sid=(str(cfg["sid"]).strip() if cfg.get("sid") else None),
                  user=str(cfg.get("user") or "").strip(),
                  schema_owner=str(cfg.get("schema_owner") or cfg.get("user") or "").strip(),
                  wallet_dir=cfg.get("wallet_dir"))
        out.validate()
        return out

    def validate(self) -> None:
        assert HOST.match(self.host), "host must be a host name or IP address"
        assert 0 < self.port < 65536, "port must be 1-65535"
        assert bool(self.service_name) != bool(self.sid), "give either a service name or a SID"
        assert SERVICE.match(self.service_name or self.sid or ""), "service name / SID has invalid characters"
        assert IDENT.match(self.user), "user must be an Oracle user name"
        assert IDENT.match(self.schema_owner), "schema owner must be an Oracle schema name"

    def dsn(self) -> str:
        """Easy-connect for a service name; a full descriptor for a SID (easy-connect has no SID form)."""
        if self.service_name:
            return f"{self.host}:{self.port}/{self.service_name}"
        return (f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={self.host})(PORT={self.port}))"
                f"(CONNECT_DATA=(SID={self.sid})))")


def is_transient(exc: BaseException) -> bool:
    text = str(exc)
    return any(code in text for code in TRANSIENT)


def with_retry(fn: Callable[[], T], attempts: int = 4, base_delay: float = 1.5,
               sleep: Callable[[float], None] = time.sleep) -> T:
    """Exponential backoff (1.5s, 3s, 6s) on transient Oracle network errors only."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            sleep(base_delay * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")


def write_wallet(wallet_zip: bytes) -> str:
    """Unpack an Autonomous Database wallet (.zip) to a private temporary folder; returns the folder."""
    import io
    import zipfile

    folder = tempfile.mkdtemp(prefix="ora_wallet_")
    with zipfile.ZipFile(io.BytesIO(wallet_zip)) as z:
        for member in z.namelist():
            name = os.path.basename(member)
            if name and not name.startswith("."):
                with open(os.path.join(folder, name), "wb") as fh:
                    fh.write(z.read(member))
    return folder


def connect(cfg: OracleConfig, password: str):
    """An open oracledb connection. Thin mode handles mTLS wallets (ewallet.pem) without Instant Client."""
    import oracledb

    assert password, "the Oracle password is missing"
    kwargs: Dict[str, Any] = {"user": cfg.user, "password": password, "dsn": cfg.dsn(),
                              "tcp_connect_timeout": cfg.connect_timeout}
    if cfg.wallet_dir:
        kwargs.update(config_dir=cfg.wallet_dir, wallet_location=cfg.wallet_dir)
        if cfg.wallet_password:
            kwargs["wallet_password"] = cfg.wallet_password
    conn = with_retry(lambda: oracledb.connect(**kwargs))
    conn.call_timeout = 15 * 60 * 1000  # a single round trip may not hang a job for ever
    return conn


def cursor(conn, arraysize: int = ARRAY_SIZE):
    cur = conn.cursor()
    cur.arraysize = arraysize
    cur.prefetchrows = arraysize
    return cur
