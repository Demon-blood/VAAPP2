import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import get_settings


def _fernet() -> Fernet:
    raw = get_settings().token_encryption_key.encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(raw)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a valid urlsafe base64 Fernet key") from exc
    if len(decoded) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return Fernet(raw)


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored credential could not be decrypted") from exc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)
