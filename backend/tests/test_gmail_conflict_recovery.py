from __future__ import annotations

import json

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.integrations.google_api import (
    _execute_google_request,
    _google_http_status,
    ensure_gmail_label,
    ensure_gmail_labels,
    modify_gmail_message,
)


def _http_error(status: int, reason: str = "conflict") -> HttpError:
    response = Response({"status": str(status)})
    content = json.dumps(
        {
            "error": {
                "code": status,
                "errors": [{"reason": reason, "message": reason}],
                "message": reason,
            }
        }
    ).encode()
    return HttpError(response, content)


class _Request:
    def __init__(self, callback):
        self._callback = callback

    def execute(self):
        return self._callback()


@pytest.mark.asyncio
async def test_google_request_retries_409_at_operation_boundary(monkeypatch):
    attempts = {"count": 0}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.integrations.google_api.asyncio.sleep", no_sleep)

    def factory():
        def execute():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise _http_error(409, "aborted")
            return {"ok": True}

        return _Request(execute)

    result = await _execute_google_request(factory, attempts=4)
    assert result == {"ok": True}
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_google_request_does_not_retry_non_transient_400(monkeypatch):
    attempts = {"count": 0}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.integrations.google_api.asyncio.sleep", no_sleep)

    def factory():
        def execute():
            attempts["count"] += 1
            raise _http_error(400, "badRequest")

        return _Request(execute)

    with pytest.raises(HttpError) as caught:
        await _execute_google_request(factory, attempts=4)
    assert _google_http_status(caught.value) == 400
    assert attempts["count"] == 1


class _FakeLabels:
    def __init__(self):
        self.labels: list[dict[str, str]] = []
        self.create_attempts = 0

    def list(self, *, userId: str):
        assert userId == "me"
        return _Request(lambda: {"labels": list(self.labels)})

    def create(self, *, userId: str, body: dict):
        assert userId == "me"

        def execute():
            self.create_attempts += 1
            # Simulate another Render worker winning the create race between our
            # list and create calls. Gmail reports 409; the next list sees it.
            if self.create_attempts == 1:
                self.labels.append({"id": "Label_42", "name": body["name"]})
                raise _http_error(409, "duplicate")
            raise AssertionError("ensure_gmail_label should re-list after the conflict")

        return _Request(execute)


class _FakeUsers:
    def __init__(self, labels: _FakeLabels):
        self._labels = labels

    def labels(self):
        return self._labels


class _FakeService:
    def __init__(self):
        self.fake_labels = _FakeLabels()
        self._users = _FakeUsers(self.fake_labels)

    def users(self):
        return self._users


@pytest.mark.asyncio
async def test_ensure_gmail_label_recovers_when_other_worker_creates_label(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.integrations.google_api.asyncio.sleep", no_sleep)
    service = _FakeService()

    label_id = await ensure_gmail_label(None, "Mail/00 Status/Actie nodig", service=service)

    assert label_id == "Label_42"
    assert service.fake_labels.create_attempts == 1


class _EventuallyVisibleConflictLabels:
    def __init__(self):
        self.list_calls = 0
        self.create_attempts = 0

    def list(self, *, userId: str):
        assert userId == "me"

        def execute():
            self.list_calls += 1
            # Simulate Gmail returning 409 before labels.list exposes the winner.
            if self.list_calls < 4:
                return {"labels": []}
            return {"labels": [{"id": "Label_99", "name": "MAIL/00 STATUS/ACTIE NODIG"}]}

        return _Request(execute)

    def create(self, *, userId: str, body: dict):
        assert userId == "me"

        def execute():
            self.create_attempts += 1
            raise _http_error(409, "aborted")

        return _Request(execute)


@pytest.mark.asyncio
async def test_ensure_gmail_labels_waits_for_eventually_visible_normalized_conflict(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.integrations.google_api.asyncio.sleep", no_sleep)
    labels = _EventuallyVisibleConflictLabels()
    service = type("Service", (), {"users": lambda self: _FakeUsers(labels)})()

    resolved = await ensure_gmail_labels(None, ["Mail/00 Status/Actie nodig"], service=service)

    assert resolved == {"Mail/00 Status/Actie nodig": "Label_99"}
    assert labels.create_attempts == 1
    assert labels.list_calls >= 4


class _PermanentConflictLabels:
    def __init__(self):
        self.create_attempts = 0

    def list(self, *, userId: str):
        assert userId == "me"
        return _Request(lambda: {"labels": []})

    def create(self, *, userId: str, body: dict):
        assert userId == "me"

        def execute():
            self.create_attempts += 1
            raise _http_error(409, "aborted")

        return _Request(execute)


class _FakeMessages:
    def __init__(self):
        self.modified = []

    def modify(self, *, userId: str, id: str, body: dict):
        assert userId == "me"

        def execute():
            self.modified.append({"id": id, "body": body})
            return {"id": id}

        return _Request(execute)


class _ConflictUsers:
    def __init__(self, labels, messages):
        self._labels = labels
        self._messages = messages

    def labels(self):
        return self._labels

    def messages(self):
        return self._messages


class _ConflictService:
    def __init__(self):
        self.labels_api = _PermanentConflictLabels()
        self.messages_api = _FakeMessages()
        self._users = _ConflictUsers(self.labels_api, self.messages_api)

    def users(self):
        return self._users


@pytest.mark.asyncio
async def test_modify_message_does_not_fail_sync_for_permanent_label_409(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.integrations.google_api.asyncio.sleep", no_sleep)
    service = _ConflictService()

    async def fake_gmail_service(_db):
        return service

    monkeypatch.setattr("app.integrations.google_api.gmail_service", fake_gmail_service)
    await modify_gmail_message(None, "msg-1", add_labels=["Mail/00 Status/Actie nodig"])

    assert service.labels_api.create_attempts == 1
    assert service.messages_api.modified == []
