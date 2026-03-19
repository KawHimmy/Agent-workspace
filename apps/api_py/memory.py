from __future__ import annotations

import re
from typing import Any

import httpx

from .config import settings
from .store import list_preferences_by_user, upsert_preference


def _extract_preference_candidates(text: str) -> list[tuple[str, str]]:
    rules = [
        ("output_style", r"(简洁|详细|口语化|正式)"),
        ("target_role", r"(前端|后端|全栈|AI|算法|产品|设计)"),
        ("language", r"(中文|英文)"),
    ]
    results: list[tuple[str, str]] = []
    for key, pattern in rules:
        match = re.search(pattern, text)
        if match:
            results.append((key, match.group(0)))
    return results


async def retrieve_memory_context(user_id: str, query: str) -> dict[str, Any]:
    local_preferences = await list_preferences_by_user(user_id)
    local_text = "\n".join(f"{item['key']}: {item['value']}" for item in local_preferences)

    if not settings.mem0_api_key:
        return {"memory_text": local_text, "items": local_preferences, "source": "local"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.mem0.ai/v2/memories/search/",
                headers={
                    "Authorization": f"Token {settings.mem0_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "query": query,
                    "filters": {"OR": [{"user_id": user_id}]},
                    "version": "v2",
                    "top_k": 6,
                },
            )
            response.raise_for_status()
            items = response.json()
    except Exception:
        return {"memory_text": local_text, "items": local_preferences, "source": "local-fallback"}

    return {
        "memory_text": "\n".join(item["memory"] for item in items),
        "items": items,
        "source": "mem0",
    }


async def write_conversation_memory(
    user_id: str, user_message: str, assistant_message: str
) -> dict[str, str]:
    for key, value in _extract_preference_candidates(f"{user_message}\n{assistant_message}"):
        await upsert_preference(
            user_id=user_id,
            key=key,
            value=value,
            source="mem0+local" if settings.mem0_api_key else "local",
        )

    if not settings.mem0_api_key:
        return {"source": "local-only"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.mem0.ai/v1/memories/",
                headers={
                    "Authorization": f"Token {settings.mem0_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "user_id": user_id,
                    "messages": [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": assistant_message},
                    ],
                    "metadata": {"channel": "react-agent-workspace", "stage": "mvp"},
                },
            )
            response.raise_for_status()
            return {"source": "mem0"}
    except Exception:
        return {"source": "local-fallback"}
