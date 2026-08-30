from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

EXPECTED_BASELINE = "2bfed2996167dbc440bb4f2a7b95f13c987f8a86"
ORIGINAL_APPLICATOR = "apply_v109_briefing_ledger.py"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _normalize_generated_source(root: Path) -> None:
    service = root / "backend/app/services/briefing_delivery.py"
    tests = root / "backend/tests/test_v109_briefing_delivery.py"
    contract = root / "backend/tests/test_v109_briefing_ledger_contract.py"

    _replace_once(
        service,
        "from datetime import datetime, timedelta, timezone\n",
        "from datetime import UTC, datetime, timedelta\n",
    )
    _replace_once(
        service,
        'parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))',
        "parsed = datetime.fromisoformat(value)",
    )
    _replace_once(
        service,
        "parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)",
        "parsed = parsed.astimezone(UTC).replace(tzinfo=None)",
    )
    _replace_once(
        service,
        "delivered_at = datetime.utcnow()",
        "delivered_at = datetime.now(UTC).replace(tzinfo=None)",
    )

    text = tests.read_text(encoding="utf-8")
    import_anchor = "from datetime import datetime, timedelta\n"
    if text.count(import_anchor) != 1:
        raise RuntimeError("unexpected datetime import in v1.0.9 briefing delivery tests")
    text = text.replace(import_anchor, "from datetime import UTC, datetime, timedelta\n", 1)

    # These tests intentionally exercise the application's existing naive-UTC DB contract.
    # Construct the values from explicit UTC timestamps, then remove tzinfo at the boundary.
    literal_count = text.count("datetime(2026,")
    if literal_count != 10:
        raise RuntimeError(
            f"unexpected datetime literal count in v1.0.9 briefing delivery tests: {literal_count}"
        )
    text = text.replace("datetime(2026,", "_naive_utc(2026,")

    helper_anchor = "\n\n@pytest.fixture\nasync def db():\n"
    helper = (
        "\n\ndef _naive_utc(*parts: int) -> datetime:\n"
        "    return datetime(*parts, tzinfo=UTC).replace(tzinfo=None)\n"
        "\n\n@pytest.fixture\nasync def db():\n"
    )
    if text.count(helper_anchor) != 1:
        raise RuntimeError("unexpected fixture anchor in v1.0.9 briefing delivery tests")
    text = text.replace(helper_anchor, helper, 1)
    tests.write_text(text, encoding="utf-8")

    _replace_once(
        contract,
        'assert "delivered_at = datetime.utcnow()" in service',
        'assert "delivered_at = datetime.now(UTC).replace(tzinfo=None)" in service',
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply VAAPP v1.0.9 and normalize generated UTC handling for the release Ruff gate."
    )
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()

    applicator = Path(__file__).resolve().with_name(ORIGINAL_APPLICATOR)
    if not applicator.is_file():
        raise RuntimeError(f"missing original v1.0.9 applicator: {applicator}")

    subprocess.run([sys.executable, str(applicator), str(root)], check=True)
    _normalize_generated_source(root)

    print("Applied v1.0.9 r2 UTC/Ruff normalization.")


if __name__ == "__main__":
    main()
