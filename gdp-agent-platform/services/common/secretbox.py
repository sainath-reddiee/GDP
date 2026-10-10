"""Authenticated encryption of stored secrets in the application (pure).

Secrets (Jira refresh and access tokens, Airflow push secrets, Teams webhook URLs) are sealed here with AES-256-GCM and
stored as ciphertext in their BINARY columns. Neither the key nor the plaintext is ever part of a SQL statement: the
connector binds parameters client side, so anything bound ends up in the statement text and in Snowflake's
QUERY_HISTORY. Only the sealed bytes are bound.

Format: b"gdp1:" + 12 byte nonce + ciphertext and tag. The AES key is derived with HKDF-SHA256 from the host key
(AIP_SECRET_KEY or JIRA_TOKEN_KEY, see ops_key and jira_key) and the purpose, so one host key never encrypts two kinds
of secret with the same AES key. The purpose and a context (the row the secret belongs to) are the associated data:
a ciphertext copied to another row or column does not open.

Values written by the old Snowflake ENCRYPT() have no prefix. They are never decrypted (that would need the key in a
statement again): open_secret raises LegacySecret and the caller asks the user to enter the secret again.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = b"gdp1:"
NONCE_BYTES = 12
MIN_KEY_CHARS = 16
_SALT = b"gdp-secretbox-v1"

JIRA_TOKEN = "jira-token"
PUSH_SECRET = "push-secret"
TEAMS_WEBHOOK = "teams-webhook"


class SecretError(Exception):
    """The value cannot be opened: wrong key, tampered or truncated."""


class LegacySecret(SecretError):
    """The value was stored by Snowflake ENCRYPT() before secrets were sealed in the application."""


def ops_key() -> str:
    """The host key for ops secrets (push secrets, webhooks): AIP_SECRET_KEY, else JIRA_TOKEN_KEY; empty when unset."""
    key = (os.environ.get("AIP_SECRET_KEY") or os.environ.get("JIRA_TOKEN_KEY") or "").strip()
    return key if len(key) >= MIN_KEY_CHARS else ""


def jira_key() -> str:
    """The host key for Jira tokens: JIRA_TOKEN_KEY; empty when unset."""
    key = (os.environ.get("JIRA_TOKEN_KEY") or "").strip()
    return key if len(key) >= MIN_KEY_CHARS else ""


@lru_cache(maxsize=32)
def _aes(key: str, purpose: str) -> AESGCM:
    if len(key or "") < MIN_KEY_CHARS:
        raise SecretError("no encryption key on this host")
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT,
                   info=b"gdp1:" + purpose.encode("utf-8")).derive(key.encode("utf-8"))
    return AESGCM(derived)


def _aad(purpose: str, context: str) -> bytes:
    return f"{purpose}\x00{context}".encode("utf-8")


def seal(plaintext: str, key: str, purpose: str, context: str = "") -> bytes:
    """The sealed bytes for a BINARY column."""
    nonce = os.urandom(NONCE_BYTES)
    return PREFIX + nonce + _aes(key, purpose).encrypt(nonce, str(plaintext).encode("utf-8"), _aad(purpose, context))


def as_bytes(value: Any) -> Optional[bytes]:
    """A BINARY value as the connector returns it (bytes or bytearray); a hex string is accepted too."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        try:
            return bytes.fromhex(value)
        except ValueError:
            return value.encode("utf-8")
    raise SecretError("unexpected stored value")


def is_legacy(value: Any) -> bool:
    data = as_bytes(value)
    return data is not None and not data.startswith(PREFIX)


def open_secret(value: Any, key: str, purpose: str, context: str = "") -> Optional[str]:
    """The plaintext of a sealed value; None for NULL. LegacySecret for an old ENCRYPT() value, SecretError when the
    key is wrong or the value was changed."""
    data = as_bytes(value)
    if data is None:
        return None
    if not data.startswith(PREFIX):
        raise LegacySecret("the value was stored in the old format")
    body = data[len(PREFIX):]
    if len(body) < NONCE_BYTES + 16:
        raise SecretError("the stored value is truncated")
    try:
        plain = _aes(key, purpose).decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], _aad(purpose, context))
    except InvalidTag:
        raise SecretError("the stored value cannot be opened with this host's key") from None
    return plain.decode("utf-8")
