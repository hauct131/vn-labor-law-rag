"""PBKDF2 password hashing helpers using only Python's standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_SALT_BYTES = 16
_DERIVED_KEY_BYTES = 32
_DUMMY_HASHES: dict[int, str] = {}


def hash_password(password: str, *, iterations: int) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_DERIVED_KEY_BYTES,
    )
    return "$".join(
        (
            _ALGORITHM,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_text)
        if iterations < 1:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


def verify_against_dummy(password: str, *, iterations: int) -> None:
    encoded = _DUMMY_HASHES.get(iterations)
    if encoded is None:
        encoded = hash_password("not-the-user-password", iterations=iterations)
        _DUMMY_HASHES[iterations] = encoded
    verify_password(password, encoded)
