"""Symmetric encryption for user secrets at rest (per-user API keys).

Key is derived from JWT_SECRET — adequate for a friends-scale deployment where the
threat model is 'database file leaks without the env'. Rotating JWT_SECRET invalidates
stored secrets (users re-enter their keys)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str | None:
    """None if undecryptable (e.g. JWT_SECRET rotated)."""
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None
