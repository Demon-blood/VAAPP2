from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_v071_historical_statement_feature_contract_remains_present() -> None:
    root = _root()
    assert (root / "backend" / "app" / "services" / "beobank_statement_parser.py").exists()
    assert (root / "backend" / "app" / "services" / "bank_statement_import.py").exists()

def test_statement_import_is_exposed_in_budget_ui_and_backend() -> None:
    root = _root()
    page = (root / "android" / "lib" / "screens" / "finance_autopilot_page.dart").read_text()
    state = (root / "android" / "lib" / "app_state.dart").read_text()
    routes = (root / "backend" / "app" / "api" / "routes.py").read_text()
    assert "Import financial history" in page
    assert "FilePicker.pickFiles" in page
    assert "file_picker: ^11.0.3" in (root / "android" / "pubspec.yaml").read_text()
    assert "/api/finance/statements/import" in state
    assert '@router.post("/api/finance/statements/import")' in routes


def test_scope_and_role_are_not_conflated() -> None:
    root = _root()
    accounts = (root / "android" / "lib" / "screens" / "accounts_page.dart").read_text()
    finance = (root / "android" / "lib" / "screens" / "finance_autopilot_page.dart").read_text()
    assert "DropdownMenuItem(value: 'reserve', child: Text('Reserve only'))" not in accounts
    assert "'operating', 'spending', 'savings', 'reserve', 'tax', 'income', 'disabled'" in finance


def test_historical_financial_evidence_uses_new_tables() -> None:
    root = _root()
    entities = (root / "backend" / "app" / "models" / "entities.py").read_text()
    assert 'class BankStatementImport(Base):' in entities
    assert 'class HistoricalFinancialTransaction(Base):' in entities
    assert '__tablename__ = "bank_statement_imports"' in entities
    assert '__tablename__ = "historical_financial_transactions"' in entities


def test_terms_cleanup_catches_branded_localized_names() -> None:
    root = _root()
    policy = (root / "backend" / "app" / "services" / "document_policy.py").read_text()
    assert "conditions?|voorwaarden" in policy
    assert "document_category_decision" in policy


def test_live_bank_history_is_recategorized_under_current_policy() -> None:
    root = _root()
    finance = (root / "backend" / "app" / "services" / "financial_autopilot.py").read_text()
    main = (root / "backend" / "app" / "main.py").read_text()
    assert '"digital": ("google play"' in finance
    assert '"subscriptions": ("subscription", "abonnement", "netflix"' in finance
    assert "async def recategorize_bank_transaction_history" in finance
    assert "await recategorize_bank_transaction_history(db)" in main


def test_document_self_categories_are_visible_in_work_filters() -> None:
    root = _root()
    work = (root / "android" / "lib" / "screens" / "work_page.dart").read_text()
    assert "category.contains('important')" in work
