from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_uncertain_own_transfer_is_system_owned_and_never_creates_fake_needs_you() -> None:
    source = _text("backend/app/services/financial_autopilot.py")

    assert 'transfer.status = "creation_uncertain"' in source
    assert 'transfer.requires_user_action = False' in source
    assert 'title="Check bank before retrying own-account transfer"' not in source
    assert 'source_type="bank_transfer_uncertain"' not in source
    assert 'if transfer.external_payment_id is None:' in source
    assert 'transfer.authorization_url = None' in source


def test_uncertain_transfer_recovery_requires_unique_booked_provider_evidence() -> None:
    recovery = _text("backend/app/services/own_transfer_recovery.py")

    for marker in (
        "reconcile_uncertain_own_account_transfer",
        "reconcile_all_uncertain_own_account_transfers",
        '_transaction_direction(row) != "debit"',
        "amount != _decimal(transfer.amount)",
        "currency != transfer.currency.upper()",
        "_destination_iban(row) != destination_iban",
        "abs((transaction_date.date() - transfer.created_at.date()).days)",
        "if len(candidates) != 1:",
        'state = "ambiguous" if len(candidates) > 1 else "waiting_for_evidence"',
        '"automatic_retry": False',
        'transfer.status = "completed"',
        '"completion_evidence": "booked_bank_transaction"',
    ):
        assert marker in recovery


def test_recovery_evidence_binds_one_bank_transaction_to_one_transfer() -> None:
    entities = _text("backend/app/models/entities.py")

    assert "class OwnAccountTransferRecoveryEvidence(Base):" in entities
    evidence_class = entities.split("class OwnAccountTransferRecoveryEvidence", 1)[1].split("class ", 1)[0]
    assert "unique=True" in evidence_class
    assert "uq_own_transfer_recovery_account_transaction" in entities
    assert '"bank_account_id"' in entities
    assert '"transaction_id"' in entities


def test_financial_autopilot_reconciles_existing_uncertainty_without_redial() -> None:
    source = _text("backend/app/services/financial_autopilot.py")

    assert "from app.services.own_transfer_recovery import" in source
    assert "reconcile_uncertain_own_account_transfer" in source
    assert "reconcile_all_uncertain_own_account_transfers" in source
    assert "await reconcile_uncertain_own_account_transfer(db, transfer)" in source
    assert "await reconcile_all_uncertain_own_account_transfers(db)" in source


def test_v114_release_identity_and_historical_evidence_are_preserved() -> None:
    version = _text("backend/app/core/version.py")
    pubspec = _text("android/pubspec.yaml")
    status = _text("STATUS.md")
    state = _text("VAAPP_PROJECT_STATE.json")
    handoff = _text("VAAPP_PROJECT_HANDOFF.md")

    assert 'APP_VERSION = "1.0.14"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.14"' in version
    assert "version: 1.0.14+57" in pubspec
    assert "v1.0.13" in status
    assert "v1.0.12" in status
    assert "v1.0.11" in status
    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
