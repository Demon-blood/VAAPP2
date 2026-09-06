from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.google_api import list_gmail_history_added_message_ids
from app.services import gmail_sync_service


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _MailboxState:
    def __init__(self, history_id: str = "100") -> None:
        self.account_key = "owner@example.com"
        self.history_id = history_id
        self.watch_topic = ""
        self.watch_expiration_at = None
        self.last_push_at = None
        self.last_history_sync_at = None
        self.last_full_sync_at = None
        self.last_watch_renewed_at = None
        self.last_error = ""


@pytest.mark.asyncio
async def test_watch_renewal_never_advances_processing_cursor(monkeypatch) -> None:
    state = _MailboxState("100")
    db = _FakeDb()
    expires = datetime.now(UTC) + timedelta(days=6)

    async def fake_runtime_value(_db, key: str, default: str = "") -> str:
        assert key == "google_pubsub_topic"
        return "projects/vaapp/topics/gmail"

    async def fake_mailbox_state(_db):
        return state

    async def fake_watch(_db, topic: str):
        assert topic == "projects/vaapp/topics/gmail"
        return {
            "historyId": "200",
            "expiration": str(int(expires.timestamp() * 1000)),
        }

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gmail_sync_service, "get_runtime_value", fake_runtime_value)
    monkeypatch.setattr(gmail_sync_service, "mailbox_state", fake_mailbox_state)
    monkeypatch.setattr(gmail_sync_service, "start_gmail_watch", fake_watch)
    monkeypatch.setattr(gmail_sync_service, "write_audit", fake_audit)

    result = await gmail_sync_service.ensure_gmail_watch(db, force=True)

    assert state.history_id == "100"
    assert result["history_id"] == "100"
    assert result["watch_history_id"] == "200"
    assert state.watch_topic == "projects/vaapp/topics/gmail"
    assert state.watch_expiration_at is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_profile_refresh_is_observational_by_default(monkeypatch) -> None:
    state = _MailboxState("100")
    db = _FakeDb()

    async def fake_mailbox_state(_db):
        return state

    async def fake_profile(_db):
        return {"emailAddress": "owner@example.com", "historyId": "250"}

    monkeypatch.setattr(gmail_sync_service, "mailbox_state", fake_mailbox_state)
    monkeypatch.setattr(gmail_sync_service, "get_gmail_profile", fake_profile)

    await gmail_sync_service.refresh_mailbox_cursor_from_profile(db)
    assert state.history_id == "100"

    await gmail_sync_service.refresh_mailbox_cursor_from_profile(
        db,
        advance_processing_cursor=True,
    )
    assert state.history_id == "250"


@pytest.mark.asyncio
async def test_full_recovery_checkpoints_pre_scan_cursor_then_catches_up(monkeypatch) -> None:
    state = _MailboxState("")
    db = _FakeDb()
    observed: list[str] = []

    async def fake_mailbox_state(_db):
        return state

    async def fake_profile(_db):
        observed.append("profile")
        return {"emailAddress": "owner@example.com", "historyId": "300"}

    async def fake_sync(_db, max_messages: int):
        observed.append(f"scan:{max_messages}:{state.history_id}")
        assert state.history_id == ""
        return 4

    async def fake_history_sync(_db, **_kwargs):
        observed.append(f"history:{state.history_id}")
        assert state.history_id == "300"
        state.history_id = "325"
        return {"mode": "history", "processed": 2, "history_id": "325"}

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gmail_sync_service, "mailbox_state", fake_mailbox_state)
    monkeypatch.setattr(gmail_sync_service, "get_gmail_profile", fake_profile)
    monkeypatch.setattr(gmail_sync_service, "sync_gmail", fake_sync)
    monkeypatch.setattr(gmail_sync_service, "history_sync", fake_history_sync)
    monkeypatch.setattr(gmail_sync_service, "write_audit", fake_audit)

    result = await gmail_sync_service.full_recovery_sync(db, max_messages=500)

    assert observed == ["profile", "scan:500:", "history:300"]
    assert result["processed"] == 6
    assert result["bootstrap_processed"] == 4
    assert result["catchup_processed"] == 2
    assert result["history_id"] == "325"
    assert db.commits >= 2


class _Request:
    def __init__(self, callback):
        self._callback = callback

    def execute(self):
        return self._callback()


class _HistoryApi:
    def __init__(self) -> None:
        self.calls = 0

    def list(self, **_kwargs):
        def execute():
            self.calls += 1
            return {
                "historyId": str(100 + self.calls),
                "history": [
                    {
                        "id": str(100 + self.calls),
                        "messagesAdded": [
                            {"message": {"id": f"msg-{self.calls}", "labelIds": ["INBOX"]}}
                        ],
                    }
                ],
                "nextPageToken": f"page-{self.calls}",
            }

        return _Request(execute)


class _UsersApi:
    def __init__(self, history: _HistoryApi) -> None:
        self._history = history

    def history(self):
        return self._history


class _GmailService:
    def __init__(self) -> None:
        self.history_api = _HistoryApi()
        self._users = _UsersApi(self.history_api)

    def users(self):
        return self._users


@pytest.mark.asyncio
async def test_history_page_limit_fails_closed_instead_of_returning_advanced_cursor(monkeypatch) -> None:
    service = _GmailService()

    async def fake_service(_db):
        return service

    monkeypatch.setattr("app.integrations.google_api.gmail_service", fake_service)

    with pytest.raises(RuntimeError, match="pagination limit"):
        await list_gmail_history_added_message_ids(
            None,
            start_history_id="100",
            label_id="INBOX",
            max_pages=2,
        )

    assert service.history_api.calls == 2
