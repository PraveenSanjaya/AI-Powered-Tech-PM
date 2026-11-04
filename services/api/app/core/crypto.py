"""Encryption for integration credentials. Key comes from the environment only."""
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CryptoError(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise CryptoError("CREDENTIAL_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("CREDENTIAL_ENCRYPTION_KEY is invalid") from exc


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise CryptoError("Stored credential cannot be decrypted with the current key") from exc
