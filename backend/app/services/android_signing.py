from __future__ import annotations

import base64
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization.pkcs12 import serialize_key_and_certificates
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_text
from app.integrations.github_api import github_get, github_put
from app.models.entities import RuntimeSetting
from app.services.runtime_config import get_runtime_value

_KEYSTORE_KEY = "android_signing_keystore_b64"
_STORE_PASSWORD_KEY = "android_signing_store_password"
_KEY_ALIAS_KEY = "android_signing_key_alias"
_KEY_PASSWORD_KEY = "android_signing_key_password"
_CERT_FINGERPRINT_KEY = "android_signing_cert_sha256"
_ALIAS = "full-time-va"
_SECRET_NAMES = {
    "ANDROID_KEYSTORE_BASE64": _KEYSTORE_KEY,
    "ANDROID_KEYSTORE_PASSWORD": _STORE_PASSWORD_KEY,
    "ANDROID_KEY_ALIAS": _KEY_ALIAS_KEY,
    "ANDROID_KEY_PASSWORD": _KEY_PASSWORD_KEY,
}


async def _set_internal(db: AsyncSession, key: str, value: str, *, is_secret: bool = True) -> None:
    row = await db.get(RuntimeSetting, key)
    if row is None:
        row = RuntimeSetting(key=key, value_encrypted=encrypt_text(value), is_secret=is_secret)
        db.add(row)
    else:
        row.value_encrypted = encrypt_text(value)
        row.is_secret = is_secret
    await db.commit()


def _new_signing_material() -> dict[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Full-Time VA"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Full-Time VA Android Release"),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365 * 30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    password = secrets.token_urlsafe(36)
    p12 = serialize_key_and_certificates(
        _ALIAS.encode("utf-8"),
        private_key,
        certificate,
        None,
        serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return {
        _KEYSTORE_KEY: base64.b64encode(p12).decode("ascii"),
        _STORE_PASSWORD_KEY: password,
        _KEY_ALIAS_KEY: _ALIAS,
        _KEY_PASSWORD_KEY: password,
        _CERT_FINGERPRINT_KEY: fingerprint,
    }


async def ensure_signing_material(db: AsyncSession) -> dict[str, str]:
    existing = {
        key: await get_runtime_value(db, key)
        for key in (
            _KEYSTORE_KEY,
            _STORE_PASSWORD_KEY,
            _KEY_ALIAS_KEY,
            _KEY_PASSWORD_KEY,
            _CERT_FINGERPRINT_KEY,
        )
    }
    if all(existing.values()):
        return existing

    created = _new_signing_material()
    for key, value in created.items():
        await _set_internal(db, key, value, is_secret=key != _CERT_FINGERPRINT_KEY)
    return created


def _repository_parts(repository: str) -> tuple[str, str]:
    value = repository.strip().removesuffix(".git")
    if value.startswith("https://github.com/"):
        value = value[len("https://github.com/") :]
    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:") :]
    parts = [part for part in value.split("/") if part]
    if len(parts) != 2:
        raise ValueError("GitHub repository must be owner/name or an HTTPS GitHub repository URL")
    return parts[0], parts[1]


async def _encrypt_for_github(db: AsyncSession, owner: str, repo: str, value: str) -> tuple[str, str]:
    from nacl.public import PublicKey, SealedBox

    key_info = await github_get(db, f"/repos/{owner}/{repo}/actions/secrets/public-key")
    public_key = PublicKey(base64.b64decode(key_info["key"]))
    encrypted = SealedBox(public_key).encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii"), str(key_info["key_id"])


async def _put_github_secret(db: AsyncSession, owner: str, repo: str, name: str, value: str) -> None:
    encrypted_value, key_id = await _encrypt_for_github(db, owner, repo, value)
    await github_put(
        db,
        f"/repos/{owner}/{repo}/actions/secrets/{name}",
        {"encrypted_value": encrypted_value, "key_id": key_id},
    )


async def install_repository_signing(db: AsyncSession, repository: str = "") -> dict[str, Any]:
    target = repository.strip() or (await get_runtime_value(db, "github_default_repository")).strip()
    if not target:
        raise ValueError("Configure the GitHub default repository first")
    owner, repo = _repository_parts(target)
    material = await ensure_signing_material(db)

    for github_name, internal_key in _SECRET_NAMES.items():
        await _put_github_secret(db, owner, repo, github_name, material[internal_key])

    return {
        "configured": True,
        "repository": f"{owner}/{repo}",
        "fingerprint_sha256": material[_CERT_FINGERPRINT_KEY],
        "secret_names": sorted(_SECRET_NAMES),
        "message": "Persistent Android release signing is installed. Do not rotate this key after installing the first stable-signed APK.",
    }


async def repository_signing_status(db: AsyncSession, repository: str = "") -> dict[str, Any]:
    target = repository.strip() or (await get_runtime_value(db, "github_default_repository")).strip()
    fingerprint = await get_runtime_value(db, _CERT_FINGERPRINT_KEY)
    if not target:
        return {"configured": False, "repository": "", "fingerprint_sha256": fingerprint}
    owner, repo = _repository_parts(target)
    result = await github_get(db, f"/repos/{owner}/{repo}/actions/secrets")
    names = {str(item.get("name")) for item in result.get("secrets", [])}
    return {
        "configured": all(name in names for name in _SECRET_NAMES),
        "repository": f"{owner}/{repo}",
        "fingerprint_sha256": fingerprint,
        "secret_names": sorted(name for name in _SECRET_NAMES if name in names),
    }
