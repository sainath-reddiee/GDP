import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from app.db import _quote_role


def test_quote_role():
    assert _quote_role("SYSADMIN") == "SYSADMIN"
    assert _quote_role("MY-ROLE") == '"MY-ROLE"'
