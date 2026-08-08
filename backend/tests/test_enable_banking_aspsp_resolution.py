from __future__ import annotations

import pytest

from app.integrations.enable_banking import EnableBankingConfigurationError, resolve_aspsp


def test_resolve_beobank_from_provider_catalog_name() -> None:
    aspsps = [
        {"name": "Argenta", "psu_types": ["personal"]},
        {"name": "Beobank NV/SA", "psu_types": ["personal"]},
    ]
    assert resolve_aspsp(aspsps, "Beobank", "personal")["name"] == "Beobank NV/SA"


def test_resolve_revolut_from_provider_catalog_name() -> None:
    aspsps = [
        {"name": "Revolut Bank UAB", "psu_types": ["personal", "business"]},
        {"name": "KBC", "psu_types": ["personal"]},
    ]
    assert resolve_aspsp(aspsps, "Revolut", "personal")["name"] == "Revolut Bank UAB"


def test_resolve_rejects_incompatible_psu_type() -> None:
    aspsps = [{"name": "Example Bank", "psu_types": ["business"]}]
    with pytest.raises(EnableBankingConfigurationError):
        resolve_aspsp(aspsps, "Example Bank", "personal")

@pytest.mark.asyncio
async def test_start_authorization_uses_rfc3339_and_bank_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations import enable_banking

    async def fake_list_aspsps(*args, **kwargs):
        return [
            {
                "name": "Beobank NV/SA",
                "psu_types": ["personal"],
                "maximum_consent_validity": 3600,
            }
        ]

    captured: dict = {}

    async def fake_request(db, method, path, *, json_body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = json_body
        return {"url": "https://auth.enablebanking.com/example"}

    monkeypatch.setattr(enable_banking, "list_aspsps", fake_list_aspsps)
    monkeypatch.setattr(enable_banking, "_request", fake_request)

    result = await enable_banking.start_account_authorization(
        object(),
        institution_country="BE",
        institution_name="Beobank",
        psu_type="personal",
        state="state",
        redirect_url="https://example.com/api/banking/callback",
    )

    assert result["url"].startswith("https://auth.enablebanking.com/")
    assert result["_resolved_aspsp_name"] == "Beobank NV/SA"
    assert captured["method"] == "POST"
    assert captured["path"] == "/auth"
    access = captured["body"]["access"]
    assert "T" in access["valid_until"]
    assert access["valid_until"].endswith("+00:00")
    assert access["balances"] is True
    assert access["transactions"] is True
