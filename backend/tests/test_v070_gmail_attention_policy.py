from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_ai_prompt_separates_preserve_from_inbox() -> None:
    source = (_root() / "backend" / "app" / "integrations" / "ai_client.py").read_text()
    assert "`preserve` means retain" in source
    assert "does NOT mean keep it in the Inbox" in source
    assert "Routine informational mail should set archive=true" in source
    assert "Low-value promotions/newsletters/social/routine notifications should" in source


def test_reconciliation_v2_repairs_existing_inbox() -> None:
    source = (_root() / "backend" / "app" / "services" / "email_processor.py").read_text()
    block = source.split("async def reconcile_v070_processed_inbox", 1)[1].split("def _safe_low_value_for_trash", 1)[0]
    assert 'marker = "v070_gmail_attention_policy_reconciled_v2"' in block
    assert 'q="in:inbox"' in block
    assert "_normalize_retention_policy" in block
    assert 'remove_labels.append("INBOX")' in block
    assert 'remove_labels.append("Mail/00 Status/Belangrijk bewaren")' in block
