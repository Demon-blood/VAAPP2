from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_release_version_alignment() -> None:
    root = _root()
    assert 'APP_VERSION = "1.0.18"' in (root / "backend/app/core/version.py").read_text()
    assert 'version = "1.0.18"' in (root / "backend/pyproject.toml").read_text()
    assert "version: 1.0.18+61" in (root / "android/pubspec.yaml").read_text()
    workflow = (root / ".github/workflows/android-release.yml").read_text()
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_budget_import_accepts_pdf_and_xlsx() -> None:
    root = _root()
    page = (root / "android/lib/screens/finance_autopilot_page.dart").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    assert "allowedExtensions: const ['pdf', 'xlsx']" in page
    assert "Choose bank files" in page
    assert "import_statement_file_bytes" in routes
    assert "pending.sort" in routes


def test_revolut_import_is_dependency_light_and_fail_closed() -> None:
    root = _root()
    parser = (root / "backend/app/services/revolut_statement_parser.py").read_text()
    pyproject = (root / "backend/pyproject.toml").read_text().casefold()
    assert "zipfile" in parser
    assert "xml.etree.elementtree" in parser.casefold()
    assert "balance progression" in parser.casefold()
    assert "openpyxl" not in pyproject
    assert "pandas" not in pyproject


def test_dual_format_merge_and_internal_transfer_propagation_exist() -> None:
    root = _root()
    service = (root / "backend/app/services/bank_statement_import.py").read_text()
    assert "authoritative_source" in service
    assert '"xlsx"' in service and '"pdf"' in service
    assert "reconcile_cross_statement_internal_transfers" in service
    assert "beobank_revolut_topup_pair" in service
    assert "propagated_internal_to_bank" in service


def test_refunds_reduce_learned_spending() -> None:
    root = _root()
    finance = (root / "backend/app/services/financial_autopilot.py").read_text()
    assert "is_refund" in finance
    assert 'direction == "credit" and not is_refund' in finance
    assert '"premium plan fee"' in finance
    assert '"metal plan fee"' in finance


def test_private_bank_files_are_not_packaged() -> None:
    root = _root()
    assert not list(root.rglob("*.xlsx"))
    assert not list(root.rglob("*.pdf"))
