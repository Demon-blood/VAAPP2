from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_v095_release_identity() -> None:
    root = _root()
    assert 'APP_VERSION = "1.0.7"' in (root / "backend/app/core/version.py").read_text()
    assert 'version = "1.0.7"' in (root / "backend/pyproject.toml").read_text()
    assert 'version: 1.0.7+50' in (root / "android/pubspec.yaml").read_text()
    assert "Full-Time-VA-Android-v1.0.5.apk" in (root / ".github/workflows/android-release.yml").read_text()


def test_document_intelligence_and_form_ledgers_are_additive_and_sensitive_values_are_encrypted() -> None:
    models = (_root() / "backend/app/models/entities.py").read_text()
    for model in ("DocumentIntelligence", "UserProfileFact", "DocumentObligation", "FormSubmission"):
        assert f"class {model}" in models
    for field in ("extracted_text_encrypted", "value_encrypted", "fields_encrypted"):
        assert field in models
    assert 'correlation_key: Mapped[str] = mapped_column(String(64), unique=True' in models
    assert 'idempotency_key: Mapped[str] = mapped_column(String(255), unique=True' in models
    assert 'due_at: Mapped[datetime | None]' in models
    assert 'browser_operation_id' in models
    assert 'completed_at' in models


def test_document_intelligence_reads_real_drive_content_and_exact_source_dates() -> None:
    source = (_root() / "backend/app/services/document_ownership.py").read_text()
    assert "MediaIoBaseDownload" in source
    assert "fitz.open" in source
    assert "get_gmail_message" in source
    assert "extract_gmail_body" in source
    assert "encrypt_text(text)" in source
    assert "_deadline_candidates" in source
    assert "_DATE_PATTERNS" in source
    assert "_MONTH_PATTERN" in source
    assert "_DEADLINE_SIGNALS" in source
    assert "OCR" not in source


def test_forms_fill_before_side_effect_and_provider_postcondition_is_required() -> None:
    root = _root()
    docs = (root / "backend/app/services/document_ownership.py").read_text()
    browser = (root / "backend/app/services/browser_operator.py").read_text()
    assert '"kind": "autofill_form"' in docs
    assert '"side_effect": False' in docs
    assert '"kind": "click_action"' in docs
    assert '"side_effect": True' in docs
    assert '"replay_safe": False' in docs
    assert '"text_any_contains"' in docs
    assert 'kind in {"click", "press", "click_action"}' in browser
    assert '"form_input"' in browser
    assert "_required_form_fields_missing" in browser
    assert "_perform_click_action" in browser
    assert 'operation.status = "creation_uncertain"' in browser
    assert "will not blindly replay" in browser


def test_form_completion_requires_verified_browser_operation() -> None:
    source = (_root() / "backend/app/services/document_ownership.py").read_text()
    assert 'if operation.status == "verified":' in source
    assert 'row.status = "completed"' in source
    assert 'submission.status = "verified"' in source
    assert 'row.completed_at = operation.verified_at or now' in source
    assert 'if operation.status in {"creation_uncertain", "failed"}:' in source
    assert 'row.status = "blocked_system"' in source
    assert 'operation.status == "needs_user_auth"' in source
    assert 'operation.challenge_type == "form_input"' in source


def test_documents_run_in_durable_worker_and_use_phase5_browser_executor() -> None:
    root = _root()
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    docs = (root / "backend/app/services/document_ownership.py").read_text()
    assert '@job_handler("documents.reconcile")' in workflow
    assert "reconcile_document_ownership" in workflow
    assert '@job_handler("housekeeping.documents")' in workflow
    assert '"document_ownership": ownership' in workflow
    assert "prepare_browser_operation" in docs
    assert 'event_type="browser_portal_operation_planned"' in docs
    assert 'source_type="document_form"' in docs
    assert "operation_requires_material_decision" in docs
    core = (root / "backend/app/services/autonomous_core.py").read_text()
    assert 'event.event_type == "document_obligation_blocked"' in core
    assert 'category="documents_forms_deadlines"' in core
    assert 'status="blocked_capability"' in core
    assert 'due_at' in core


def test_document_ownership_api_and_android_surface_are_wired() -> None:
    root = _root()
    routes = (root / "backend/app/api/routes.py").read_text()
    state = (root / "android/lib/app_state.dart").read_text()
    work = (root / "android/lib/screens/work_page.dart").read_text()
    for route in (
        "/api/documents/ownership/status",
        "/api/documents/obligations",
        "/api/documents/obligations/{obligation_id}",
        "/api/documents/reconcile",
        "/api/documents/profile-facts",
    ):
        assert route in routes
    assert '"document_forms_deadlines"' in routes
    assert "documentOwnershipStatus" in state
    assert "documentObligations" in state
    assert "documentProfileFacts" in state
    assert "reconcileDocumentsNow" in state
    assert "setDocumentProfileFact" in state
    assert "Document ownership" in work
    assert "Add verified profile fact for form filling" in work
    assert "_DocumentObligationCard" in work


def test_exact_deadline_parser_handles_english_and_dutch_without_guessing_years() -> None:
    # Execute the parser directly from its source nodes. The upload overlay intentionally
    # contains only changed files, so this local contract must not require importing every
    # unchanged baseline integration module. GitHub CI imports the complete application.
    import ast
    from datetime import datetime
    import re

    source = (_root() / "backend/app/services/document_ownership.py").read_text()
    tree = ast.parse(source)
    wanted = {"_DATE_PATTERNS", "_MONTHS", "_MONTH_PATTERN", "_DEADLINE_SIGNALS", "_deadline_candidates"}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = []
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
            if wanted.intersection(names):
                nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"re": re, "datetime": datetime}
    exec(compile(module, "document_ownership_deadline_contract", "exec"), namespace)
    parser = namespace["_deadline_candidates"]

    rows = parser(
        "Please submit the application by 21/09/2026. Uiterlijk 03-10-2026 ontvangen wij het formulier."
    )
    assert [item.date().isoformat() for item in rows] == ["2026-09-21", "2026-10-03"]
    # Day/month without an explicit year is deliberately not promoted to a durable deadline.
    assert parser("Please return this form by 21/09.") == []
