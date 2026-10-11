"""Shared fixtures for the unit tests."""

import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_read_caches():
    """The API keeps short read caches (apps/api/app/perf.py); tests swap fake databases between requests for the
    same user, so every test starts with them empty."""
    perf = sys.modules.get("app.perf")
    if perf is not None:
        perf.bump()
    yield

