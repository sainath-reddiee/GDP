"""Connections to Oracle with python-oracledb: thin mode (no Instant Client), plain TCP or TLS (TCPS, e.g. Autonomous
Database without a wallet), an optional wallet folder on the API host, and retry with backoff on transient network
errors. Every session runs in UTC and names itself so a DBA can see who is reading."""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, TypeVar
from urllib.parse import parse_qs

HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
SERVICE = re.compile(r"^[A-Za-z0-9_.$#\-]{1,128}$")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
# Socket drops, listener down, timeouts, broken connections: worth retrying. Anything else (bad password,
# missing table, privilege) fails at once.
TRANSIENT = ("ORA-03113", "ORA-03114", "ORA-03135", "ORA-12541", "ORA-12170", "ORA-12535", "ORA-12537",
             "ORA-12547", "ORA-12571", "DPY-4011", "DPY-6005", "DPI-1080")
ARRAY_SIZE = 10_000
PROGRAM = "AgenticPipeline"
T = TypeVar("T")


@dataclass
class OracleConfig:
    host: str
    port: int = 1521
    service_name: Optional[str] = None
    sid: Optional[str] = None
    user: str = ""
    schema_owner: str = ""
    protocol: str = "tcp"                 # tcp | tcps
    ssl_server_dn_match: bool = True
    wallet_dir: Optional[str] = None      # API host only: folder with ewallet.pem / tnsnames.ora
    wallet_password: Optional[str] = field(default=None, repr=False)
    connect_timeout: int = 20

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "OracleConfig":
        out = cls(host=str(cfg.get("host") or "").strip(), port=int(cfg.get("port") or 1521),
                  service_name=(str(cfg["service_name"]).strip() if cfg.get("service_name") else None),
                  sid=(str(cfg["sid"]).strip() if cfg.get("sid") else None),
                  user=str(cfg.get("user") or "").strip(),
                  schema_owner=str(cfg.get("schema_owner") or cfg.get("user") or "").strip(),
                  protocol=str(cfg.get("protocol") or "tcp").lower(),
                  ssl_server_dn_match=str(cfg.get("ssl_server_dn_match", "true")).lower() != "false",
                  wallet_dir=cfg.get("wallet_dir") or None,
                  wallet_password=os.environ.get(cfg["wallet_password_env"]) if cfg.get("wallet_password_env") else None)
        out.validate()
        return out

    def validate(self) -> None:
        assert HOST.match(self.host), "host must be a host name or IP address (no scheme, no port)"
        assert 0 < self.port < 65536, "port must be 1-65535"
        assert bool(self.service_name) != bool(self.sid), "give either a service name or a SID"
        assert SERVICE.match(self.service_name or self.sid or ""), "service name / SID has invalid characters"
        assert IDENT.match(self.user), "user must be an Oracle user name (letters, digits, _ $ #)"
        assert IDENT.match(self.schema_owner), "schema owner must be an Oracle schema name"
        assert self.protocol in ("tcp", "tcps"), "protocol must be tcp or tcps"

    def dsn(self) -> str:
        """A full descriptor: works for service names and SIDs, TCP and TLS alike."""
        target = f"(SERVICE_NAME={self.service_name})" if self.service_name else f"(SID={self.sid})"
        security = ""
        if self.protocol == "tcps":
            security = f"(SECURITY=(SSL_SERVER_DN_MATCH={'YES' if self.ssl_server_dn_match else 'NO'}))"
        return (f"(DESCRIPTION=(CONNECT_TIMEOUT={self.connect_timeout})(RETRY_COUNT=0)"
                f"(ADDRESS=(PROTOCOL={self.protocol.upper()})(HOST={self.host})(PORT={self.port}))"
                f"(CONNECT_DATA={target}){security})")


def parse_connect_string(text: str) -> Dict[str, Any]:
    """What a user pastes -> connection fields. Accepts easy connect (host:port/service, //host/service,
    tcps://host:1522/service?ssl_server_dn_match=no), JDBC URLs (jdbc:oracle:thin:@host:port:SID or @//host/service)
    and TNS descriptors ((DESCRIPTION=(ADDRESS=(PROTOCOL=TCPS)(HOST=..)(PORT=..))(CONNECT_DATA=(SERVICE_NAME=..))))."""
    raw = (text or "").strip()
    assert raw, "paste a connect string"
    if "(" in raw:
        def grab(key: str) -> Optional[str]:
            found = re.search(rf"\(\s*{key}\s*=\s*([^)\s]+)\s*\)", raw, re.IGNORECASE)
            return found.group(1) if found else None

        host, port = grab("HOST"), grab("PORT")
        assert host, "no HOST found in the descriptor"
        out: Dict[str, Any] = {"host": host, "port": int(port or 1521),
                               "protocol": (grab("PROTOCOL") or "TCP").lower()}
        service, sid = grab("SERVICE_NAME"), grab("SID")
        assert service or sid, "no SERVICE_NAME or SID found in the descriptor"
        out["service_name" if service else "sid"] = service or sid
        dn = grab("SSL_SERVER_DN_MATCH")
        if dn:
            out["ssl_server_dn_match"] = dn.upper() in ("YES", "TRUE", "ON")
        return out
    s = re.sub(r"^jdbc:oracle:thin:", "", raw, flags=re.IGNORECASE)
    if "@" in s:                                       # user/password@host... : never keep credentials
        s = s.rsplit("@", 1)[1]
    protocol = "tcp"
    scheme = re.match(r"^(tcps?)://", s, re.IGNORECASE)
    if scheme:
        protocol, s = scheme.group(1).lower(), s[scheme.end():]
    s = s.lstrip("/")
    query: Dict[str, str] = {}
    if "?" in s:
        s, qs = s.split("?", 1)
        query = {k.lower(): v[-1] for k, v in parse_qs(qs).items()}
    sid_form = re.match(r"^([^:/]+):(\d+):([A-Za-z0-9_$#]+)$", s)   # host:port:SID (JDBC)
    if sid_form:
        return {"host": sid_form.group(1), "port": int(sid_form.group(2)), "sid": sid_form.group(3),
                "protocol": protocol}
    found = re.match(r"^([^:/]+)(?::(\d+))?/([^:/]+)(?::\w+)?(?:/\w+)?$", s)
    assert found, "could not read host:port/service from that text"
    out = {"host": found.group(1), "port": int(found.group(2) or (1522 if protocol == "tcps" else 1521)),
           "service_name": found.group(3), "protocol": protocol}
    if "ssl_server_dn_match" in query:
        out["ssl_server_dn_match"] = query["ssl_server_dn_match"].lower() in ("yes", "true", "on")
    return out


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


def connect_kwargs(cfg: OracleConfig, password: str) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"user": cfg.user, "password": password, "dsn": cfg.dsn(),
                              "tcp_connect_timeout": cfg.connect_timeout, "program": PROGRAM,
                              "driver_name": f"{PROGRAM} : oracledb"}
    if cfg.wallet_dir:
        kwargs.update(config_dir=cfg.wallet_dir, wallet_location=cfg.wallet_dir)
        if cfg.wallet_password:
            kwargs["wallet_password"] = cfg.wallet_password
    return kwargs


def prepare_session(conn) -> None:
    """UTC session (TIMESTAMP WITH LOCAL TIME ZONE reads as UTC), a name the DBA can see, and a call timeout so a
    single round trip cannot hang a job for ever."""
    conn.call_timeout = 15 * 60 * 1000
    try:
        conn.module, conn.action = PROGRAM, "read"
    except Exception:
        pass
    cur = conn.cursor()
    try:
        cur.execute("ALTER SESSION SET TIME_ZONE = 'UTC'")
    finally:
        cur.close()


def connect(cfg: OracleConfig, password: str, retry: bool = True):
    """An open, prepared oracledb connection (thin mode)."""
    import oracledb

    assert password, "the Oracle password is missing"
    kwargs = connect_kwargs(cfg, password)

    def attempt():
        try:
            return oracledb.connect(**kwargs)
        except TypeError:  # an older oracledb without program / driver_name
            plain = {k: v for k, v in kwargs.items() if k not in ("program", "driver_name")}
            return oracledb.connect(**plain)

    conn = with_retry(attempt) if retry else attempt()
    prepare_session(conn)
    return conn


def cursor(conn, arraysize: int = ARRAY_SIZE):
    cur = conn.cursor()
    cur.arraysize = arraysize
    cur.prefetchrows = arraysize
    return cur
