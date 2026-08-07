import base64
import os

os.environ.setdefault("PUBLIC_BASE_URL", "https://example.test")
os.environ.setdefault("PAIRING_SECRET", "x" * 32)
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"1" * 32).decode())

from app.core.crypto import decrypt_text, encrypt_text, hash_token


def test_encryption_round_trip():
    encrypted = encrypt_text("secret-value")
    assert encrypted != "secret-value"
    assert decrypt_text(encrypted) == "secret-value"


def test_token_hash_is_stable():
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abcd")
