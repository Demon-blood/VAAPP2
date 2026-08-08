from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_config import get_runtime_value


class GitHubConfigurationError(RuntimeError):
    pass


async def _token(db: AsyncSession) -> str:
    token = await get_runtime_value(db, "github_token")
    if not token:
        raise GitHubConfigurationError("GitHub token is not configured")
    return token


async def _headers(db: AsyncSession) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {await _token(db)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def github_request(
    db: AsyncSession,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    async with httpx.AsyncClient(base_url="https://api.github.com", timeout=60) as client:
        response = await client.request(
            method,
            path,
            headers=await _headers(db),
            params=params,
            json=payload,
        )
        response.raise_for_status()
        return response.json() if response.content else {}


async def github_get(db: AsyncSession, path: str, params: dict[str, Any] | None = None) -> Any:
    return await github_request(db, "GET", path, params=params)


async def github_post(db: AsyncSession, path: str, payload: dict[str, Any]) -> Any:
    return await github_request(db, "POST", path, payload=payload)


async def github_put(db: AsyncSession, path: str, payload: dict[str, Any]) -> Any:
    return await github_request(db, "PUT", path, payload=payload)


async def verify_github_connection(db: AsyncSession) -> dict[str, Any]:
    return await github_get(db, "/user")


async def list_repositories(db: AsyncSession) -> list[dict[str, Any]]:
    return await github_get(
        db,
        "/user/repos",
        params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
    )


async def list_notifications(db: AsyncSession) -> list[dict[str, Any]]:
    """Return personal GitHub notifications when the configured token supports them.

    GitHub's personal notifications REST endpoints accept classic PATs only. The VA
    deliberately uses a fine-grained PAT for repository Actions/secrets access, so a
    `github_pat_...` token must not make the whole VA dashboard unhealthy. A classic
    token can still be used here if a user explicitly configures one in the future.
    """
    token = await _token(db)
    if token.startswith("github_pat_"):
        return []
    try:
        result = await github_get(
            db,
            "/notifications",
            params={"all": "false", "participating": "false", "per_page": 100},
        )
        return list(result or [])
    except httpx.HTTPError:
        # Personal GitHub notifications are an optional convenience feature.
        # They must never make the VA dashboard unhealthy if GitHub rejects,
        # rate-limits, times out, or temporarily fails this endpoint. Repository,
        # Actions, releases, issues, and signing automation use separate calls.
        return []


async def create_issue(
    db: AsyncSession,
    repository: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    if "/" not in repository:
        raise ValueError("repository must be in owner/name format")
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    return await github_post(db, f"/repos/{repository}/issues", payload)


async def dispatch_workflow(
    db: AsyncSession,
    repository: str,
    workflow_id: str = "android-release.yml",
    ref: str = "main",
) -> dict[str, Any]:
    if "/" not in repository:
        raise ValueError("repository must be in owner/name format")
    await github_post(
        db,
        f"/repos/{repository}/actions/workflows/{workflow_id}/dispatches",
        {"ref": ref},
    )
    return {
        "dispatched": True,
        "repository": repository,
        "workflow_id": workflow_id,
        "ref": ref,
        "actions_url": f"https://github.com/{repository}/actions/workflows/{workflow_id}",
    }


async def list_workflow_runs(
    db: AsyncSession,
    repository: str,
    workflow_id: str = "android-release.yml",
) -> list[dict[str, Any]]:
    result = await github_get(
        db,
        f"/repos/{repository}/actions/workflows/{workflow_id}/runs",
        params={"per_page": 20},
    )
    return list(result.get("workflow_runs") or [])
