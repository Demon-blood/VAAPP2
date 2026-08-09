from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import EmailMessage, Task
from app.schemas.api import AutomationDecision
from app.services.action_reconciler import reconcile_action_queue


@pytest.mark.asyncio
async def test_action_required_email_gets_concrete_task_and_clears_when_completed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    decision = AutomationDecision(
        category="General",
        priority="high",
        action_required=True,
        task={
            "title": "Send requested document",
            "description": "Send the requested document to the sender.",
            "requires_approval": True,
        },
        reasoning_summary="A concrete follow-up is required.",
    )
    async with session_factory() as db:
        email = EmailMessage(
            provider_message_id="message-1",
            thread_id="thread-1",
            sender="sender@example.com",
            subject="Document request",
            action_required=True,
            status="processed",
            analysis_json=decision.model_dump_json(),
        )
        db.add(email)
        await db.commit()

        first = await reconcile_action_queue(db)
        assert first["created_tasks"] == 1
        task = (
            await db.execute(
                select(Task).where(Task.source_id == "message-1", Task.status == "open")
            )
        ).scalar_one()
        assert task.title == "Send requested document"
        assert email.action_required is True

        task.status = "completed"
        await db.commit()
        second = await reconcile_action_queue(db)
        await db.refresh(email)
        assert second["resolved"] == 1
        assert email.action_required is False

    await engine.dispose()


def test_android_dashboard_cards_are_actionable_and_branded() -> None:
    root = Path(__file__).parents[2]
    dashboard = (root / "android/lib/screens/dashboard_page.dart").read_text()
    shell = (root / "android/lib/screens/home_shell.dart").read_text()
    common = (root / "android/lib/widgets/common_widgets.dart").read_text()
    pubspec = (root / "android/pubspec.yaml").read_text()
    workflow = (root / ".github/workflows/android-release.yml").read_text()

    assert "Run VA now" in dashboard
    assert "onOpenEmails" in dashboard
    assert "onOpenTasks" in dashboard
    assert "onOpenBills" in dashboard
    assert "onOpenPayments" in dashboard
    assert "onTap: onTap" in common
    assert "inboxActionOnly = true" in shell
    assert "moneyTab = 1" in shell
    assert "assets/app_icon.png" in pubspec
    assert "tooling/app_icon/ic_launcher_${density}.png" in workflow
