from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .config import settings

INITIAL_STORE: dict[str, list[dict[str, Any]]] = {
    "conversations": [],
    "messages": [],
    "agentRuns": [],
    "documents": [],
    "backgroundJobs": [],
    "userPreferences": [],
}

_store_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_store() -> None:
    settings.store_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings.store_file.exists():
        _write_store_unlocked(deepcopy(INITIAL_STORE))


def _normalize_json(raw: str) -> str:
    # Some editors on Windows prepend a UTF-8 BOM, which JSON.parse/json.loads rejects.
    return raw.lstrip("\ufeff").strip()


def _write_store_unlocked(store: dict[str, list[dict[str, Any]]]) -> None:
    settings.store_file.write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def read_store() -> dict[str, list[dict[str, Any]]]:
    async with _store_lock:
        await ensure_store()
        raw = settings.store_file.read_text(encoding="utf-8")
        normalized = _normalize_json(raw)
        if not normalized:
            _write_store_unlocked(deepcopy(INITIAL_STORE))
            return deepcopy(INITIAL_STORE)

        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            _write_store_unlocked(deepcopy(INITIAL_STORE))
            return deepcopy(INITIAL_STORE)


async def write_store(store: dict[str, list[dict[str, Any]]]) -> None:
    async with _store_lock:
        _write_store_unlocked(store)


def _sorted_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: item.get("updatedAt") or item.get("createdAt") or "",
        reverse=True,
    )


async def list_conversations_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [item for item in store["conversations"] if item["userId"] == user_id]
    )


async def create_conversation(user_id: str, title: str = "新的任务") -> dict[str, Any]:
    store = await read_store()
    now = _now()
    conversation = {
        "id": str(uuid.uuid4()),
        "userId": user_id,
        "title": title,
        "createdAt": now,
        "updatedAt": now,
    }
    store["conversations"].append(conversation)
    await write_store(store)
    return conversation


async def get_conversation_by_id(
    conversation_id: str, user_id: str
) -> dict[str, Any] | None:
    store = await read_store()
    conversation = next(
        (
            item
            for item in store["conversations"]
            if item["id"] == conversation_id and item["userId"] == user_id
        ),
        None,
    )
    if not conversation:
        return None

    return {
        **conversation,
        "messages": [
            item
            for item in store["messages"]
            if item["conversationId"] == conversation_id
        ],
        "documents": _sorted_desc(
            [
                item
                for item in store["documents"]
                if item["conversationId"] == conversation_id
                and item["userId"] == user_id
            ]
        ),
    }


async def append_message(
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = await read_store()
    now = _now()
    message = {
        "id": str(uuid.uuid4()),
        "conversationId": conversation_id,
        "userId": user_id,
        "role": role,
        "content": content,
        "metadata": metadata or {},
        "createdAt": now,
        "updatedAt": now,
    }
    store["messages"].append(message)

    for conversation in store["conversations"]:
        if conversation["id"] == conversation_id:
            conversation["updatedAt"] = now
            if role == "user" and content.strip() and conversation["title"] == "新的任务":
                conversation["title"] = content[:30]
            break

    await write_store(store)
    return message


async def create_agent_run(
    conversation_id: str, user_id: str, prompt: str
) -> dict[str, Any]:
    store = await read_store()
    now = _now()
    run = {
        "id": str(uuid.uuid4()),
        "conversationId": conversation_id,
        "userId": user_id,
        "prompt": prompt,
        "status": "running",
        "toolCalls": [],
        "memoryContext": "",
        "result": "",
        "createdAt": now,
        "updatedAt": now,
    }
    store["agentRuns"].append(run)
    await write_store(store)
    return run


async def update_agent_run(run_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    store = await read_store()
    run = next((item for item in store["agentRuns"] if item["id"] == run_id), None)
    if not run:
        return None
    run.update(updates)
    run["updatedAt"] = _now()
    await write_store(store)
    return run


async def create_document(record: dict[str, Any]) -> dict[str, Any]:
    store = await read_store()
    now = _now()
    document = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "summary": "",
        "extractedText": "",
        "createdAt": now,
        "updatedAt": now,
        **record,
    }
    store["documents"].append(document)
    await write_store(store)
    return document


async def update_document(
    document_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    store = await read_store()
    document = next(
        (item for item in store["documents"] if item["id"] == document_id), None
    )
    if not document:
        return None
    document.update(updates)
    document["updatedAt"] = _now()
    await write_store(store)
    return document


async def get_document_by_id(
    document_id: str, user_id: str
) -> dict[str, Any] | None:
    store = await read_store()
    return next(
        (
            item
            for item in store["documents"]
            if item["id"] == document_id and item["userId"] == user_id
        ),
        None,
    )


async def list_documents_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [item for item in store["documents"] if item["userId"] == user_id]
    )


async def create_background_job(record: dict[str, Any]) -> dict[str, Any]:
    store = await read_store()
    now = _now()
    job = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "output": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
        **record,
    }
    store["backgroundJobs"].append(job)
    await write_store(store)
    return job


async def update_background_job(
    job_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    store = await read_store()
    job = next(
        (item for item in store["backgroundJobs"] if item["id"] == job_id), None
    )
    if not job:
        return None
    job.update(updates)
    job["updatedAt"] = _now()
    await write_store(store)
    return job


async def list_background_jobs_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [item for item in store["backgroundJobs"] if item["userId"] == user_id]
    )


async def upsert_preference(
    user_id: str, key: str, value: str, source: str = "app"
) -> None:
    store = await read_store()
    now = _now()
    existing = next(
        (
            item
            for item in store["userPreferences"]
            if item["userId"] == user_id and item["key"] == key
        ),
        None,
    )
    if existing:
        existing.update({"value": value, "source": source, "updatedAt": now})
    else:
        store["userPreferences"].append(
            {
                "id": str(uuid.uuid4()),
                "userId": user_id,
                "key": key,
                "value": value,
                "source": source,
                "createdAt": now,
                "updatedAt": now,
            }
        )
    await write_store(store)


async def list_preferences_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [item for item in store["userPreferences"] if item["userId"] == user_id]
    )
