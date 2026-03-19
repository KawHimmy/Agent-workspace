from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request, Response

from .config import settings


async def proxy_auth_request(request: Request, auth_path: str) -> Response:
    async with httpx.AsyncClient(timeout=30) as client:
        body = await request.body()
        upstream = await client.request(
            request.method,
            f"{settings.auth_service_url}/api/auth/{auth_path}",
            content=body if body else None,
            headers={
                "content-type": request.headers.get("content-type", "application/json"),
                "origin": settings.app_url,
                "cookie": request.headers.get("cookie", ""),
            },
        )

    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
    for cookie in upstream.headers.get_list("set-cookie"):
        response.headers.append("set-cookie", cookie)
    return response


async def get_current_user(request: Request) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.get(
            f"{settings.auth_service_url}/api/auth/get-session",
            headers={
                "origin": settings.app_url,
                "cookie": request.headers.get("cookie", ""),
            },
        )

    if upstream.status_code != 200:
        return None

    payload = upstream.json()
    if payload is None:
        return None
    return payload.get("user")


async def require_user(request: Request) -> dict[str, Any]:
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录后再继续操作。")
    return user
