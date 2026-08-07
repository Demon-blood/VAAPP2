from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_config import set_runtime_values


async def generate_enable_banking_keypair(db: AsyncSession) -> dict[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BE"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Full-Time VA"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Full-Time VA Enable Banking"),
        ]
    )
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    await set_runtime_values(db, {"enable_banking_private_key_pem": private_pem})
    return {
        "certificate_pem": certificate_pem,
        "sha256_fingerprint": certificate.fingerprint(hashes.SHA256()).hex(":"),
        "valid_from": certificate.not_valid_before_utc.isoformat(),
        "valid_until": certificate.not_valid_after_utc.isoformat(),
    }
