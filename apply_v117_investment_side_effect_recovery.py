from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "830c2c87b89972bc0735028584285f2827ac4bf9"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256: dict[str, str] = {'preview/backend/app/services/investment_recovery.py': 'b674045ebd37ffc6452252167b50401dd0fd742d3406dcbf8fb68356e849b238',
 'preview/backend/tests/test_v117_investment_side_effect_recovery.py': '232a23a1c72f274c2c70e53a7bac151d85c6e3af41ee6a2ec7a1681c78817b18',
 'preview/backend/tests/test_v117_investment_side_effect_recovery_contract.py': 'b55f633d9eec779ab8a21fb285660867c28877e1642a423ab7112a4b5a11ce6c',
 'preview/docs/V1.0.17_INVESTMENT_SIDE_EFFECT_RECOVERY.md': 'f091351ef77d2cb53c05b11c4d658ce150df4be67c79bb0192a3c1c31d150711'}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def replace_once(path: Path, old: str, new: str) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one anchor in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def verify_bundle() -> None:
    if not EXPECTED_PREVIEW_SHA256:
        raise RuntimeError("bundle hashes were not finalized")
    for relative, expected in EXPECTED_PREVIEW_SHA256.items():
        path = BUNDLE_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"missing prepared bundle file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"bundle integrity mismatch for {relative}: {actual}")


def verify_repo(root: Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"{root} is not a git working tree")
    head = run_git(root, "rev-parse", "HEAD")
    if head != EXPECTED_BASELINE:
        raise RuntimeError(
            f"refusing to patch unexpected HEAD {head}; expected v1.0.16 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.16"\nREQUIRED_ANDROID_VERSION = "1.0.16"\n'
    ):
        raise RuntimeError("v1.0.16 backend baseline identity mismatch")
    if "version: 1.0.16+59" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.16 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_models(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    old = '''\n\nclass BudgetEnvelope(Base):\n    __tablename__ = "budget_envelopes"\n'''
    new = '''\n\nclass InvestmentFundingRecoveryEvidence(Base):
    __tablename__ = "investment_funding_recovery_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_id: Mapped[int] = mapped_column(
        ForeignKey("investment_funding_transfers.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        index=True,
    )
    transaction_id: Mapped[str] = mapped_column(String(255))
    match_basis: Mapped[str] = mapped_column(String(120))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bank_account_id",
            "transaction_id",
            name="uq_investment_funding_recovery_account_transaction",
        ),
    )


class InvestmentTradeIntent(Base):
    __tablename__ = "investment_trade_intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_id: Mapped[int] = mapped_column(
        ForeignKey("investment_funding_transfers.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    client_order_id: Mapped[str] = mapped_column(String(18), unique=True, index=True)
    pair: Mapped[str] = mapped_column(String(40), default="")
    eur_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(40), default="prepared", index=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    provider_status: Mapped[str] = mapped_column(String(40), default="")
    observed_order_json: Mapped[str] = mapped_column(Text, default="{}")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BudgetEnvelope(Base):
    __tablename__ = "budget_envelopes"
'''
    replace_once(path, old, new)


def patch_kraken_api(root: Path) -> None:
    path = root / "backend/app/integrations/kraken_api.py"
    replace_once(
        path,
        '''class KrakenConfigurationError(RuntimeError):\n    pass\n''',
        '''class KrakenConfigurationError(RuntimeError):
    pass


class KrakenProviderHTTPError(KrakenConfigurationError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class KrakenOrderCreationUncertainError(RuntimeError):
    pass
''',
    )
    replace_once(
        path,
        '''    if response.status_code >= 400:\n        raise KrakenConfigurationError(f"Kraken HTTP {response.status_code}: {response.text[:1000]}")\n''',
        '''    if response.status_code >= 400:
        message = f"Kraken HTTP {response.status_code}: {response.text[:1000]}"
        if response.status_code == 429 or response.status_code >= 500:
            raise KrakenProviderHTTPError(message, status_code=response.status_code)
        raise KrakenConfigurationError(message)
''',
    )
    helper = '''\n\nasync def get_orders_by_client_order_id(
    db: AsyncSession,
    client_order_id: str,
) -> list[dict[str, Any]]:
    """Read open and closed Spot orders for one durable Kraken client order id."""
    client_order_id = str(client_order_id or "").strip()[:18]
    if not client_order_id:
        raise KrakenConfigurationError("Kraken client order id is required for reconciliation")
    open_result = await _private(
        db,
        "/0/private/OpenOrders",
        {"trades": False, "cl_ord_id": client_order_id},
    )
    closed_result = await _private(
        db,
        "/0/private/ClosedOrders",
        {"trades": False, "cl_ord_id": client_order_id, "without_count": "true"},
    )
    rows: list[dict[str, Any]] = []
    for source, result, key in (
        ("open", open_result, "open"),
        ("closed", closed_result, "closed"),
    ):
        values = result.get(key) if isinstance(result, dict) else None
        if not isinstance(values, dict):
            continue
        for order_id, payload in values.items():
            if not isinstance(payload, dict):
                continue
            row = dict(payload)
            row["order_id"] = str(order_id)
            row["source"] = source
            rows.append(row)
    return rows
'''
    replace_once(
        path,
        '''\n\nasync def market_buy_eur(db: AsyncSession, *, pair: str, eur_amount: Decimal, client_order_id: str) -> dict[str, Any]:\n''',
        helper
        + '''\n\nasync def market_buy_eur(db: AsyncSession, *, pair: str, eur_amount: Decimal, client_order_id: str) -> dict[str, Any]:\n''',
    )
    old = '''    return await _private(
        db,
        "/0/private/AddOrder",
        {
            "pair": pair,
            "type": "buy",
            "ordertype": "market",
            "volume": format(volume, "f"),
            "cl_ord_id": client_order_id[:18],
        },
    )
'''
    new = '''    try:
        return await _private(
            db,
            "/0/private/AddOrder",
            {
                "pair": pair,
                "type": "buy",
                "ordertype": "market",
                "volume": format(volume, "f"),
                "cl_ord_id": client_order_id[:18],
            },
        )
    except (httpx.RequestError, TimeoutError, KrakenProviderHTTPError) as exc:
        raise KrakenOrderCreationUncertainError(
            f"Kraken AddOrder outcome is uncertain: {exc}"
        ) from exc
'''
    replace_once(path, old, new)


def patch_investment_autopilot(root: Path) -> None:
    path = root / "backend/app/services/investment_autopilot.py"
    replace_once(
        path,
        '''from app.integrations.kraken_api import (
    KrakenConfigurationError,
    get_api_key_permissions,
    get_deposit_status,
    get_eur_balance,
    market_buy_eur,
)
''',
        '''from app.integrations.kraken_api import (
    KrakenConfigurationError,
    KrakenOrderCreationUncertainError,
    get_api_key_permissions,
    get_deposit_status,
    get_eur_balance,
    market_buy_eur,
)
''',
    )
    replace_once(
        path,
        '''from app.services.financial_learning import learn_recurring_cashflows\nfrom app.services.runtime_config import get_runtime_value\n''',
        '''from app.services.financial_learning import learn_recurring_cashflows
from app.services.investment_recovery import (
    prepare_kraken_trade_intent,
    reconcile_kraken_trade_intent,
    reconcile_uncertain_kraken_funding,
)
from app.services.runtime_config import get_runtime_value
''',
    )

    old_uncertain = '''    except (httpx.RequestError, TimeoutError) as exc:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = f"Kraken funding payment outcome is uncertain; automatic retry is blocked: {exc}"[:2000]
        db.add(Task(
            title="Check bank before retrying Kraken funding",
            description=transfer.failure_reason,
            source_type="kraken_funding_uncertain",
            source_id=str(transfer.id),
            priority="urgent",
            requires_approval=True,
        ))
        await db.commit()
        return {"enabled": True, "state": "creation_uncertain", "transfer_id": transfer.id}
'''
    new_uncertain = '''    except (httpx.RequestError, TimeoutError) as exc:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = False
        transfer.authorization_url = None
        transfer.failure_reason = (
            "Kraken funding payment outcome is uncertain; VA-owned bank reconciliation is active "
            f"and automatic payment replay is disabled: {exc}"
        )[:2000]
        await db.delete(state_row)
        await db.commit()
        return {"enabled": True, "state": "creation_uncertain", "transfer_id": transfer.id}
'''
    replace_once(path, old_uncertain, new_uncertain)

    old_response = '''    transfer.external_payment_id = str(response.get("payment_id") or response.get("id") or "").strip() or None
    transfer.authorization_url = str(response.get("url") or "").strip() or None
    transfer.requires_user_action = bool(transfer.authorization_url)
    if transfer.external_payment_id is None:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = "Payment provider returned no payment identifier; automatic retry is blocked."
    else:
        transfer.status = "authorization_required" if transfer.requires_user_action else str(response.get("status") or "received").lower()
    state_row.payload_json = json.dumps({"transfer_id": transfer.id, "external_payment_id": transfer.external_payment_id})
'''
    new_response = '''    transfer.external_payment_id = str(response.get("payment_id") or response.get("id") or "").strip() or None
    transfer.authorization_url = str(response.get("url") or "").strip() or None
    transfer.requires_user_action = bool(transfer.authorization_url and transfer.external_payment_id)
    if transfer.external_payment_id is None:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = False
        transfer.authorization_url = None
        transfer.failure_reason = (
            "Payment provider returned no payment identifier; VA-owned bank reconciliation is active "
            "and automatic payment replay is disabled."
        )
        await db.delete(state_row)
    else:
        transfer.status = (
            "authorization_required"
            if transfer.requires_user_action
            else str(response.get("status") or "received").lower()
        )
        state_row.payload_json = json.dumps(
            {"transfer_id": transfer.id, "external_payment_id": transfer.external_payment_id}
        )
'''
    replace_once(path, old_response, new_response)

    old_refresh = '''async def refresh_kraken_funding_transfer(db: AsyncSession, transfer: InvestmentFundingTransfer) -> InvestmentFundingTransfer:
    if not transfer.external_payment_id or transfer.status in {"creation_uncertain", "failed", "cancelled", "rejected", "funded", "invested"}:
        return transfer
    if transfer.status not in {"awaiting_deposit", "deposit_observed", "trade_pending"}:
'''
    new_refresh = '''async def refresh_kraken_funding_transfer(db: AsyncSession, transfer: InvestmentFundingTransfer) -> InvestmentFundingTransfer:
    if transfer.status in {"failed", "cancelled", "rejected", "funded", "invested"}:
        return transfer
    if not transfer.external_payment_id:
        if transfer.status == "creation_uncertain":
            await reconcile_uncertain_kraken_funding(db, transfer)
        return transfer
    if transfer.status not in {"awaiting_deposit", "deposit_observed", "trade_pending"}:
'''
    replace_once(path, old_refresh, new_refresh)

    old_trade = '''async def reconcile_kraken_funding_and_trade(db: AsyncSession, transfer: InvestmentFundingTransfer) -> InvestmentFundingTransfer:
    if transfer.status != "awaiting_deposit":
        return transfer
    current = await get_eur_balance(db)
    transfer.observed_provider_cash = current
    baseline = transfer.pre_provider_cash or Decimal("0")
    required_delta = (transfer.amount * Decimal("0.98")).quantize(Decimal("0.01"))

    # Prefer Kraken's own deposit ledger over a balance delta. The balance fallback
    # remains useful when an account/API combination does not expose fiat deposit
    # status, but it is deliberately conservative and only one funding transfer can
    # be active at a time.
    deposit_observed = False
    try:
        deposits = await get_deposit_status(db, asset="EUR")
        earliest = transfer.created_at - timedelta(days=1)
        for item in deposits:
            status = str(item.get("status") or "").casefold()
            if status not in {"success", "settled", "completed"}:
                continue
            try:
                amount = _money(item.get("amount"))
                booked = datetime.utcfromtimestamp(float(item.get("time") or 0))
            except (TypeError, ValueError, OSError):
                continue
            if booked < earliest or amount < required_delta:
                continue
            transfer.provider_deposit_ref = str(item.get("refid") or item.get("txid") or "")[:255] or None
            deposit_observed = True
            break
    except (KrakenConfigurationError, httpx.RequestError):
        deposit_observed = False

    if not deposit_observed and current - baseline < required_delta:
        await db.commit()
        return transfer
    transfer.status = "deposit_observed"
    await db.commit()

    auto_trade = (await get_runtime_value(db, "kraken_auto_trade_enabled", "false")).casefold() == "true"
    if not auto_trade:
        transfer.status = "funded"
        await db.commit()
        return transfer
    permissions = await get_api_key_permissions(db)
    if "modify-trades" not in permissions:
        transfer.status = "funded"
        transfer.failure_reason = "Kraken deposit arrived, but the API key lacks modify-trades permission; cash was left uninvested."
        await db.commit()
        return transfer
    max_trade = _money(await get_runtime_value(db, "kraken_max_auto_trade_eur", "250"), Decimal("250"))
    trade_amount = min(transfer.amount, max_trade, _money(current))
    if trade_amount <= 0:
        transfer.status = "funded"
        await db.commit()
        return transfer
    transfer.status = "trade_pending"
    await db.commit()
    try:
        result = await market_buy_eur(
            db,
            pair=transfer.trade_pair or "XBTEUR",
            eur_amount=trade_amount,
            client_order_id=f"va{transfer.id}{int(transfer.created_at.timestamp())}",
        )
    except KrakenConfigurationError as exc:
        transfer.status = "funded"
        transfer.failure_reason = f"Kraken funding succeeded but automatic trade was not placed: {exc}"[:2000]
        await db.commit()
        return transfer
    order_ids = result.get("txid") if isinstance(result, dict) else None
    if isinstance(order_ids, list):
        order_id = str(order_ids[0]) if order_ids else ""
    else:
        order_id = str(order_ids or result.get("order_id") or "") if isinstance(result, dict) else ""
    transfer.trade_order_id = order_id[:255] or None
    transfer.status = "invested" if transfer.trade_order_id else "funded"
    if not transfer.trade_order_id:
        transfer.failure_reason = "Kraken accepted the trade call but did not return an order identifier; no automatic retry was attempted."
    await write_audit(
        db,
        "kraken_investment_executed" if transfer.trade_order_id else "kraken_investment_trade_uncertain",
        entity_type="investment_funding_transfer",
        entity_id=str(transfer.id),
        result="success" if transfer.trade_order_id else "blocked",
        details={"amount": str(trade_amount), "pair": transfer.trade_pair, "order_id": transfer.trade_order_id},
    )
    await db.commit()
    return transfer
'''
    new_trade = '''async def reconcile_kraken_funding_and_trade(db: AsyncSession, transfer: InvestmentFundingTransfer) -> InvestmentFundingTransfer:
    pair = transfer.trade_pair or "XBTEUR"
    if transfer.status == "trade_pending":
        intent = await prepare_kraken_trade_intent(
            db,
            transfer,
            pair=pair,
            eur_amount=Decimal("0.00"),
            legacy_recovery=True,
        )
        await reconcile_kraken_trade_intent(db, transfer, intent)
        return transfer
    if transfer.status not in {"awaiting_deposit", "deposit_observed"}:
        return transfer

    current = await get_eur_balance(db)
    transfer.observed_provider_cash = current
    if transfer.status == "awaiting_deposit":
        baseline = transfer.pre_provider_cash or Decimal("0")
        required_delta = (transfer.amount * Decimal("0.98")).quantize(Decimal("0.01"))
        deposit_observed = False
        try:
            deposits = await get_deposit_status(db, asset="EUR")
            earliest = transfer.created_at - timedelta(days=1)
            for item in deposits:
                status = str(item.get("status") or "").casefold()
                if status not in {"success", "settled", "completed"}:
                    continue
                try:
                    amount = _money(item.get("amount"))
                    booked = datetime.utcfromtimestamp(float(item.get("time") or 0))
                except (TypeError, ValueError, OSError):
                    continue
                if booked < earliest or amount < required_delta:
                    continue
                transfer.provider_deposit_ref = (
                    str(item.get("refid") or item.get("txid") or "")[:255] or None
                )
                deposit_observed = True
                break
        except (KrakenConfigurationError, httpx.RequestError):
            deposit_observed = False
        if not deposit_observed and current - baseline < required_delta:
            await db.commit()
            return transfer
        transfer.status = "deposit_observed"
        await db.commit()

    auto_trade = (await get_runtime_value(db, "kraken_auto_trade_enabled", "false")).casefold() == "true"
    if not auto_trade:
        transfer.status = "funded"
        await db.commit()
        return transfer
    permissions = await get_api_key_permissions(db)
    required_trade_permissions = {"modify-trades", "query-open-trades", "query-closed-trades"}
    missing_permissions = sorted(required_trade_permissions.difference(permissions))
    if missing_permissions:
        transfer.status = "funded"
        transfer.failure_reason = (
            "Kraken deposit arrived, but safe automatic trading requires API permissions: "
            + ", ".join(missing_permissions)
        )[:2000]
        await db.commit()
        return transfer
    max_trade = _money(
        await get_runtime_value(db, "kraken_max_auto_trade_eur", "250"),
        Decimal("250"),
    )
    trade_amount = min(transfer.amount, max_trade, _money(current))
    if trade_amount <= 0:
        transfer.status = "funded"
        await db.commit()
        return transfer

    intent = await prepare_kraken_trade_intent(
        db,
        transfer,
        pair=pair,
        eur_amount=trade_amount,
    )
    if intent.status in {"submitting", "creation_uncertain"}:
        transfer.status = "trade_pending"
        await reconcile_kraken_trade_intent(db, transfer, intent)
        return transfer

    intent.status = "submitting"
    intent.submitted_at = datetime.utcnow()
    transfer.status = "trade_pending"
    transfer.failure_reason = ""
    await db.commit()
    try:
        result = await market_buy_eur(
            db,
            pair=pair,
            eur_amount=trade_amount,
            client_order_id=intent.client_order_id,
        )
    except KrakenOrderCreationUncertainError as exc:
        intent.status = "creation_uncertain"
        transfer.status = "trade_pending"
        transfer.failure_reason = (
            f"Kraken market-order outcome is uncertain; read-only cl_ord_id reconciliation is active: {exc}"
        )[:2000]
        await db.commit()
        return transfer
    except httpx.RequestError as exc:
        # market_buy_eur only exposes plain RequestError before AddOrder; retrying
        # that read-only price preflight is safe because no provider side effect ran.
        intent.status = "prepared"
        intent.submitted_at = None
        transfer.status = "deposit_observed"
        transfer.failure_reason = f"Kraken trade price preflight deferred: {exc}"[:2000]
        await db.commit()
        return transfer
    except KrakenConfigurationError as exc:
        intent.status = "failed"
        transfer.status = "funded"
        transfer.failure_reason = f"Kraken funding succeeded but automatic trade was not placed: {exc}"[:2000]
        await db.commit()
        return transfer

    order_ids = result.get("txid") if isinstance(result, dict) else None
    if isinstance(order_ids, list):
        order_id = str(order_ids[0]) if order_ids else ""
    else:
        order_id = str(order_ids or result.get("order_id") or "") if isinstance(result, dict) else ""
    if order_id:
        intent.provider_order_id = order_id[:255]
        intent.status = "verified"
        intent.verified_at = datetime.utcnow()
        transfer.trade_order_id = order_id[:255]
        transfer.status = "invested"
        transfer.failure_reason = ""
    else:
        intent.status = "creation_uncertain"
        transfer.trade_order_id = None
        transfer.status = "trade_pending"
        transfer.failure_reason = (
            "Kraken accepted the trade call without a transaction id; read-only cl_ord_id reconciliation is active."
        )
    await write_audit(
        db,
        "kraken_investment_executed" if transfer.trade_order_id else "kraken_investment_trade_uncertain",
        entity_type="investment_funding_transfer",
        entity_id=str(transfer.id),
        result="success" if transfer.trade_order_id else "blocked",
        details={
            "amount": str(trade_amount),
            "pair": pair,
            "order_id": transfer.trade_order_id,
            "client_order_id": intent.client_order_id,
            "automatic_retry": False,
        },
    )
    await db.commit()
    return transfer
'''
    replace_once(path, old_trade, new_trade)

    replace_once(
        path,
        '''            if row.status == "awaiting_deposit":\n                await reconcile_kraken_funding_and_trade(db, row)\n                reconciled += 1\n''',
        '''            if row.status in {"awaiting_deposit", "deposit_observed", "trade_pending"}:
                await reconcile_kraken_funding_and_trade(db, row)
                reconciled += 1
''',
    )


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/app/services/investment_recovery.py",
        "backend/app/services/investment_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v117_investment_side_effect_recovery.py",
        "backend/tests/test_v117_investment_side_effect_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v117_investment_side_effect_recovery_contract.py",
        "backend/tests/test_v117_investment_side_effect_recovery_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.17_INVESTMENT_SIDE_EFFECT_RECOVERY.md",
        "docs/V1.0.17_INVESTMENT_SIDE_EFFECT_RECOVERY.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status_path.write_text(
        '''# VAAPP v1.0.17 — Investment Side-Effect Recovery & Human Boundary Integrity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.16 source baseline: `830c2c87b89972bc0735028584285f2827ac4bf9`
- Verified v1.0.16 GitHub Actions run: `33975481668` — success
- Verified v1.0.16 prerelease tag: `va-android-116-3-1`
- v1.0.16 release identity: backend `1.0.16`, Android `1.0.16+59`
- v1.0.16 APK SHA-256: `caf9810e4ae1c8bd9db2d9e91222ace01265bd67b7dfdfaa0b472f24787ad622`
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.16.

## v1.0.17 maintenance scope

- Kraken funding creation ambiguity stays VA-owned and never creates a fake bank-check approval task.
- Unbound funding authorization URLs are suppressed; genuine SCA remains user-bound only when an external payment id exists.
- Unique booked debit evidence from the exact source account can recover the original funding intent without a replacement payment.
- A booked bank transaction can recover only one investment funding intent.
- Automatic Kraken orders persist a durable client-order intent before AddOrder.
- Kraken OpenOrders and ClosedOrders reconcile ambiguous AddOrder outcomes by the original `cl_ord_id`.
- Automatic trading requires query-open, query-closed, and modify-trades permissions so provider ambiguity is recoverable before any order is placed.
- Network/provider ambiguity or a missing order id never authorizes a second AddOrder.
- Historical `trade_pending` rows are reconciliation-only against the legacy client-order identifier.

## Release identity

- Backend: `1.0.17`
- Required Android: `1.0.17`
- Android: `1.0.17+60`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
''',
        encoding="utf-8",
    )

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.16":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-09-05",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.16",
            "verified_baseline_android_version": "1.0.16+59",
            "verified_maintenance_actions_run_id": 33975481668,
            "verified_baseline_release_tag": "va-android-116-3-1",
            "current_phase_name": "v1.0.17 Investment Side-Effect Recovery & Human Boundary Integrity",
            "current_version": "1.0.17",
            "current_android_version": "1.0.17+60",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "v117_features": [
                "Kraken funding creation uncertainty stays VA-owned without fake Needs You work",
                "unique booked source-account debit evidence recovers the original funding intent",
                "one booked bank transaction can bind to only one investment funding intent",
                "unbound funding authorization URLs are suppressed while genuine bound SCA remains human",
                "Kraken automatic trades persist a durable client-order intent before AddOrder",
                "OpenOrders and ClosedOrders reconcile ambiguous trades by the original cl_ord_id",
                "automatic trading requires query-open and query-closed permissions before provider mutation",
                "ambiguous or no-id AddOrder outcomes never authorize blind market-order replay",
                "historical trade_pending rows are reconciliation-only against their legacy client-order id",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    invariant = (
        "investment funding or market-order uncertainty remains VA-owned and never authorizes a duplicate payment or trade"
    )
    if invariant not in invariants:
        invariants.append(invariant)
    state["invariants"] = invariants
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff_path = root / "VAAPP_PROJECT_HANDOFF.md"
    handoff = read_text(handoff_path)
    current = (
        "Current candidate: **v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity**."
    )
    if current not in handoff:
        raise RuntimeError("unexpected VAAPP_PROJECT_HANDOFF.md baseline")
    marker = "## Product objective\n"
    if marker not in handoff:
        raise RuntimeError("handoff product-objective marker is missing")
    suffix = marker + handoff.split(marker, 1)[1]
    prefix = '''# VAAPP project handoff

Updated: 2026-09-05
Repository: `Demon-blood/VAAPP2`
Branch: `main`

## Verified source of truth

The verified maintenance baseline for this release is commit `830c2c87b89972bc0735028584285f2827ac4bf9` (`v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity`). GitHub Actions run `33975481668` completed successfully end-to-end with 407 backend tests, Ruff gates, Flutter analysis/tests, Android signing, signed APK build, source verification, and prerelease publication under tag `va-android-116-3-1`.

Verified v1.0.16 release identity: backend `1.0.16` / Android `1.0.16+59`. APK SHA-256: `caf9810e4ae1c8bd9db2d9e91222ace01265bd67b7dfdfaa0b472f24787ad622`. The operator subsequently reported production deployment and phone smoke testing complete.

Historical v1.0.15 source remains `2b48b72e720a2e515e346fed253e24c131ae078a` with successful Actions run `33967944880` and tag `va-android-115-3-1`. Historical v1.0.14 source remains `8557dd449db554528ab7e111d0029faf784c996f` with successful Actions run `33961135886` and tag `va-android-114-3-1`. Historical v1.0.13 source remains `ecaa113d4461a550cb49c6046a42ecf880729346` with successful Actions run `33434347111` and tag `va-android-113-4-1`. Historical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.17` / Android `1.0.17+60`.

Current candidate: **v1.0.17 — Investment Side-Effect Recovery & Human Boundary Integrity**.

v1.0.17 keeps ambiguous Kraken funding payments and market orders under VA-owned reconciliation instead of creating fake user work or permitting blind replay. Funding ambiguity is recovered only from one uniquely matching booked debit on the exact source bank account. Automatic Kraken trading persists a durable client-order intent before AddOrder and reconciles OpenOrders/ClosedOrders by the same `cl_ord_id`; ambiguous or no-id outcomes never authorize a replacement order. Genuine bound bank SCA remains the human boundary.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass.

Next work after the v1.0.17 gate is green: **v1.x maintenance and real-world hardening**.

'''
    handoff_path.write_text(prefix + suffix, encoding="utf-8")


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.16"\nREQUIRED_ANDROID_VERSION = "1.0.16"\n',
        'APP_VERSION = "1.0.17"\nREQUIRED_ANDROID_VERSION = "1.0.17"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.16"', 'version = "1.0.17"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.16+59", "version: 1.0.17+60")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.16';\nconst String minimumBackendVersion = '1.0.16';\n",
        "const String appRelease = '1.0.17';\nconst String minimumBackendVersion = '1.0.17';\n",
    )
    replacements = (
        ('APP_VERSION = "1.0.16"', 'APP_VERSION = "1.0.17"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.16"', 'REQUIRED_ANDROID_VERSION = "1.0.17"'),
        ('version = "1.0.16"', 'version = "1.0.17"'),
        ('version: 1.0.16+59', 'version: 1.0.17+60'),
        ("appRelease = '1.0.16'", "appRelease = '1.0.17'"),
        ("minimumBackendVersion = '1.0.16'", "minimumBackendVersion = '1.0.17'"),
        ('APP_VERSION == "1.0.16"', 'APP_VERSION == "1.0.17"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v117_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected living release contracts to advance to v1.0.17")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    tracked = [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
    untracked = [
        line
        for line in run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
        if line
    ]
    changed = sorted(set(tracked + untracked))
    if not changed:
        raise RuntimeError("patch produced no changes")
    forbidden = [name for name in changed if name.startswith(".github/workflows/")]
    if forbidden:
        raise RuntimeError(f"workflow files changed unexpectedly: {forbidden}")
    required = {
        "backend/app/models/entities.py",
        "backend/app/integrations/kraken_api.py",
        "backend/app/services/investment_autopilot.py",
        "backend/app/services/investment_recovery.py",
        "backend/tests/test_v117_investment_side_effect_recovery.py",
        "backend/tests/test_v117_investment_side_effect_recovery_contract.py",
        "docs/V1.0.17_INVESTMENT_SIDE_EFFECT_RECOVERY.md",
        "backend/app/core/version.py",
        "backend/pyproject.toml",
        "android/pubspec.yaml",
        "android/lib/release_contract.dart",
        "STATUS.md",
        "VAAPP_PROJECT_STATE.json",
        "VAAPP_PROJECT_HANDOFF.md",
    }
    missing = sorted(required.difference(changed))
    if missing:
        raise RuntimeError(f"required v1.0.17 changes missing from diff: {missing}")

    models = read_text(root / "backend/app/models/entities.py")
    kraken = read_text(root / "backend/app/integrations/kraken_api.py")
    investment = read_text(root / "backend/app/services/investment_autopilot.py")
    recovery = read_text(root / "backend/app/services/investment_recovery.py")
    for marker in (
        "class InvestmentFundingRecoveryEvidence",
        "class InvestmentTradeIntent",
        "uq_investment_funding_recovery_account_transaction",
        "client_order_id",
    ):
        if marker not in models:
            raise RuntimeError(f"v1.0.17 model marker missing: {marker}")
    for marker in (
        "class KrakenOrderCreationUncertainError",
        "async def get_orders_by_client_order_id",
        '"/0/private/OpenOrders"',
        '"/0/private/ClosedOrders"',
        '"cl_ord_id": client_order_id',
    ):
        if marker not in kraken:
            raise RuntimeError(f"v1.0.17 Kraken API marker missing: {marker}")
    if 'source_type="kraken_funding_uncertain"' in investment:
        raise RuntimeError("Kraken creation uncertainty must not create fake Needs You tasks")
    for marker in (
        "reconcile_uncertain_kraken_funding",
        "prepare_kraken_trade_intent",
        "reconcile_kraken_trade_intent",
        "KrakenOrderCreationUncertainError",
        "query-open-trades",
        "query-closed-trades",
        'if transfer.status == "trade_pending":',
    ):
        if marker not in investment:
            raise RuntimeError(f"v1.0.17 investment marker missing: {marker}")
    for marker in (
        "stable_trade_client_order_id",
        "legacy_trade_client_order_id",
        "automatic_retry\": False",
        'transfer.status = "awaiting_deposit"',
        'transfer.status = "trade_pending"',
    ):
        if marker not in recovery:
            raise RuntimeError(f"v1.0.17 recovery marker missing: {marker}")


def apply(root: Path) -> None:
    verify_bundle()
    verify_repo(root)
    patch_models(root)
    patch_kraken_api(root)
    patch_investment_autopilot(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.17 source patch prepared. Changed files:")
    print(run_git(root, "diff", "--name-only"))
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard")
    if untracked:
        print(untracked)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--verify-bundle", action="store_true")
    args = parser.parse_args()
    verify_bundle()
    if args.verify_bundle:
        print("v1.0.17 bundle integrity verified")
        return
    apply(Path(args.root).resolve())


if __name__ == "__main__":
    main()
