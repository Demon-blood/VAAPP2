from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v113_release_identity() -> None:
    version = read("backend/app/core/version.py")
    assert 'APP_VERSION = "1.0.17"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.17"' in version
    assert 'version = "1.0.17"' in read("backend/pyproject.toml")
    assert "version: 1.0.17+60" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.17'" in release
    assert "minimumBackendVersion = '1.0.17'" in release


def test_gmail_ambiguity_never_expires_into_terminal_failure() -> None:
    delivery = read("backend/app/services/gmail_delivery.py")
    assert "def _gmail_uncertain_verify_delay" in delivery
    assert 'row.status in {"creation_uncertain", "sent_unverified"}' in delivery
    assert 'if row.status == "failed_uncertain":' in delivery
    assert 'row.status = "failed_uncertain"' not in delivery
    assert "continue provider reconciliation" in delivery
    assert "without resending" in delivery
    assert "timedelta(minutes=2)" in delivery
    assert "timedelta(minutes=15)" in delivery
    assert "timedelta(hours=1)" in delivery
    assert "timedelta(hours=6)" in delivery
    assert delivery.count("send_gmail_message(") == 1
    assert "Never re-POST" in delivery


def test_historical_failed_uncertain_steps_are_reopened_for_provider_reconciliation() -> None:
    core = read("backend/app/services/autonomous_core.py")
    assert "async def _recover_legacy_gmail_uncertainty" in core
    assert 'GmailOutboundMessage.status == "failed_uncertain"' in core
    assert 'step.verification_type == "gmail_outbound_verified"' in core
    assert 'step.status = "verifying"' in core
    assert "step.finished_at = None" in core
    assert "await _recover_legacy_gmail_uncertainty(db, now)" in core
    assert "gmail_outbound_legacy_uncertainty_reopened" in core


def test_objective_verifier_no_longer_terminalizes_provider_ambiguity() -> None:
    core = read("backend/app/services/autonomous_core.py")
    gmail_branch = core.split('if step.verification_type == "gmail_outbound_verified":', 1)[1].split(
        'if step.verification_type == "browser_operation_verified":', 1
    )[0]
    assert 'if outbound.status == "failed":' in gmail_branch
    assert 'if outbound.status in {"failed", "failed_uncertain"}:' not in gmail_branch
    assert "verified = await ensure_gmail_outbound_verified(db, outbound)" in gmail_branch
    assert 'await _transition_objective(db, objective, "verifying")' in gmail_branch


def test_existing_gmail_idempotency_and_human_boundary_contract_is_preserved() -> None:
    delivery = read("backend/app/services/gmail_delivery.py")
    assert "deterministic_rfc_message_id" in delivery
    assert "find_gmail_message_by_rfc_message_id" in delivery
    assert "failed_user" in delivery
    assert "user_required" in delivery
    assert "row.attempts >= row.max_attempts" in delivery
    assert "default=1" in read("backend/app/models/entities.py")
