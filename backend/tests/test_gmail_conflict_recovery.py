from __future__ import annotations

import json

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.integrations.google_api import (
    _execute_google_request,
    _google_http_status,
    ensure_gmail_label,
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
