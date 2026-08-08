import base64

from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

from app.services.android_signing import _new_signing_material


def test_generated_android_signing_keystore_is_valid_pkcs12():
    material = _new_signing_material()
    raw = base64.b64decode(material["android_signing_keystore_b64"])
    key, certificate, _ = load_key_and_certificates(
        raw,
        material["android_signing_store_password"].encode("utf-8"),
    )
    assert key is not None
    assert certificate is not None
    assert material["android_signing_key_alias"] == "full-time-va"
    assert material["android_signing_key_password"] == material["android_signing_store_password"]
    assert len(material["android_signing_cert_sha256"]) == 64
