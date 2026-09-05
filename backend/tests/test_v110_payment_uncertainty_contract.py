from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v110_release_identity_is_consistent():
    assert 'APP_VERSION = "1.0.15"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.15"' in read("backend/app/core/version.py")
    assert 'version = "1.0.15"' in read("backend/pyproject.toml")
    assert "version: 1.0.15+58" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.15'" in release
    assert "minimumBackendVersion = '1.0.15'" in release


def test_uncertain_payment_creation_is_system_owned_not_fake_needs_you():
    banking = read("backend/app/services/banking_service.py")
    network = banking.split("except (httpx.RequestError, TimeoutError) as exc:", 1)[1].split(
        "external_id =", 1
    )[0]
    assert 'payment.status = "creation_uncertain"' in network
    assert "payment.requires_user_action = False" in network
    assert "Task(" not in network

    missing_id = banking.split("if external_id is None:", 1)[1].split("else:", 1)[0]
    assert "payment.requires_user_action = False" in missing_id
    assert "payment.authorization_url = None" in missing_id


def test_real_bank_authorization_remains_a_human_boundary():
    banking = read("backend/app/services/banking_service.py")
    assert "payment.requires_user_action = bool(payment.authorization_url)" in banking
    assert 'payment.status = "authorization_required" if payment.authorization_url' in banking


def test_bill_lifecycle_reconciles_uncertainty_without_calling_it_authorization():
    workflow = read("backend/app/services/workflow_engine.py")
    assert "reconcile_uncertain_payment" in workflow
    assert '"payment_reconciling"' in workflow
    assert "payment.requires_user_action and payment.authorization_url" in workflow
    assert '"payment_uncertainty_recovery"' in workflow


def test_guardian_counts_uncertain_creation_as_system_issue_not_needs_you():
    guardian = read("backend/app/services/operational_guardian.py")
    assert 'Payment.status == "creation_uncertain"' in guardian
    assert '"system_owned_uncertainty"' in guardian
    assert 'payments["system_owned_uncertainty"]' in guardian


def test_recovery_requires_unique_provider_backed_transaction_evidence():
    service = read("backend/app/services/payment_recovery.py")
    entities = read("backend/app/models/entities.py")
    assert "class PaymentRecoveryEvidence" in entities
    assert '__tablename__ = "payment_recovery_evidence"' in entities
    assert "len(candidates) != 1" in service
    assert 'payment.status = "completed"' in service
    assert 'bill.status = "paid"' in service
    assert '"completion_evidence": "booked_bank_transaction"' in service
    assert '"automatic_retry": False' in service
