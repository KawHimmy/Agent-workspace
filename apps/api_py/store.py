from __future__ import annotations

import asyncio
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from .config import settings

try:
    import psycopg
except Exception:  # pragma: no cover - optional dependency
    psycopg = None

Store = dict[str, list[dict[str, Any]]]
StoreMutator = Callable[[Store], Any]
T = TypeVar("T")

INITIAL_STORE: Store = {
    "conversations": [],
    "messages": [],
    "agentRuns": [],
    "agentSteps": [],
    "toolCallLogs": [],
    "runEvents": [],
    "documents": [],
    "backgroundJobs": [],
    "userPreferences": [],
    "memoryWritebacks": [],
    "auditLogs": [],
    "modelUsageLogs": [],
    "rateLimitEvents": [],
    "promptVersions": [],
    "mcpServers": [],
    "mcpServerConnections": [],
    "toolRegistry": [],
    "workspaces": [],
    "workspaceMembers": [],
}

_store_lock = asyncio.Lock()
_DATABASE_STORE_ID = "react-agent-workspace"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_json(raw: str) -> str:
    return raw.lstrip("\ufeff").strip()


def _normalize_store_shape(store: dict[str, Any] | None) -> Store:
    normalized: Store = deepcopy(INITIAL_STORE)
    if not isinstance(store, dict):
        return normalized

    for key, value in store.items():
        if isinstance(value, list):
            normalized[key] = value

    return normalized


def _use_database_store() -> bool:
    return bool(settings.database_url and psycopg is not None)


def _connect_database():
    if not settings.database_url or psycopg is None:
        raise RuntimeError("Database store is not configured.")
    return psycopg.connect(settings.database_url)


def _ensure_database_store_table_unlocked() -> None:
    with _connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists app_runtime_store (
                  id text primary key,
                  payload jsonb not null default '{}'::jsonb,
                  updated_at timestamptz not null default now()
                )
                """
            )
        connection.commit()


def _write_store_to_database_unlocked(store: Store) -> None:
    _ensure_database_store_table_unlocked()
    payload = json.dumps(store, ensure_ascii=False)
    with _connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into app_runtime_store (id, payload, updated_at)
                values (%s, %s::jsonb, now())
                on conflict (id)
                do update set payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (_DATABASE_STORE_ID, payload),
            )
        connection.commit()


def _read_store_from_database_unlocked() -> Store:
    _ensure_database_store_table_unlocked()
    with _connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select payload::text from app_runtime_store where id = %s",
                (_DATABASE_STORE_ID,),
            )
            row = cursor.fetchone()

    if not row or not row[0]:
        store = deepcopy(INITIAL_STORE)
        _write_store_to_database_unlocked(store)
        return store

    try:
        parsed = json.loads(row[0])
    except json.JSONDecodeError:
        store = deepcopy(INITIAL_STORE)
        _write_store_to_database_unlocked(store)
        return store

    store = _normalize_store_shape(parsed)
    if store.keys() != parsed.keys():
        _write_store_to_database_unlocked(store)
    return store


def _write_store_unlocked(store: Store) -> None:
    if _use_database_store():
        _write_store_to_database_unlocked(store)
        return

    settings.store_file.parent.mkdir(parents=True, exist_ok=True)
    settings.store_file.write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_store_unlocked() -> Store:
    if _use_database_store():
        return _read_store_from_database_unlocked()

    settings.store_file.parent.mkdir(parents=True, exist_ok=True)

    if not settings.store_file.exists():
        store = deepcopy(INITIAL_STORE)
        _write_store_unlocked(store)
        return store

    raw = settings.store_file.read_text(encoding="utf-8")
    normalized = _normalize_json(raw)
    if not normalized:
        store = deepcopy(INITIAL_STORE)
        _write_store_unlocked(store)
        return store

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        store = deepcopy(INITIAL_STORE)
        _write_store_unlocked(store)
        return store

    store = _normalize_store_shape(parsed)
    if store.keys() != parsed.keys():
        _write_store_unlocked(store)
    return store


async def ensure_store() -> None:
    async with _store_lock:
        _read_store_unlocked()


async def read_store() -> Store:
    async with _store_lock:
        return deepcopy(_read_store_unlocked())


async def _mutate_store(mutator: StoreMutator) -> Any:
    async with _store_lock:
        store = _read_store_unlocked()
        result = mutator(store)
        _write_store_unlocked(store)
        return deepcopy(result)


def _sorted_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: item.get("updatedAt") or item.get("createdAt") or "",
        reverse=True,
    )


def _dt(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(value)


def _next_sequence(
    items: list[dict[str, Any]],
    *,
    field: str,
    value: str,
    sequence_key: str = "sequence",
) -> int:
    sequences = [
        int(item.get(sequence_key, 0))
        for item in items
        if item.get(field) == value and isinstance(item.get(sequence_key, 0), int)
    ]
    return max(sequences, default=0) + 1


def _trim_text(value: Any, limit: int = 6000) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered or f"workspace-{uuid.uuid4().hex[:8]}"


def _workspace_match(item: dict[str, Any], workspace_id: str | None) -> bool:
    return workspace_id is None or item.get("workspaceId") == workspace_id


def _hydrate_legacy_workspace_records(
    store: Store, *, user_id: str, workspace_id: str
) -> None:
    scoped_tables = [
        "conversations",
        "messages",
        "agentRuns",
        "agentSteps",
        "toolCallLogs",
        "runEvents",
        "documents",
        "backgroundJobs",
        "auditLogs",
    ]
    for table in scoped_tables:
        for item in store.get(table, []):
            if item.get("userId") == user_id and not item.get("workspaceId"):
                item["workspaceId"] = workspace_id


async def ensure_default_workspace_for_user(
    user_id: str, *, workspace_name: str | None = None
) -> dict[str, Any]:
    now = _now()
    default_name = workspace_name or "My Workspace"

    def mutator(store: Store) -> dict[str, Any]:
        membership = next(
            (item for item in store["workspaceMembers"] if item["userId"] == user_id),
            None,
        )
        if membership:
            workspace = next(
                (
                    item
                    for item in store["workspaces"]
                    if item["id"] == membership["workspaceId"]
                ),
                None,
            )
            if workspace:
                _hydrate_legacy_workspace_records(
                    store, user_id=user_id, workspace_id=workspace["id"]
                )
                return {
                    **workspace,
                    "currentRole": membership["role"],
                    "memberCount": len(
                        [
                            item
                            for item in store["workspaceMembers"]
                            if item["workspaceId"] == workspace["id"]
                        ]
                    ),
                }

        workspace = {
            "id": str(uuid.uuid4()),
            "name": default_name,
            "slug": _slugify(f"{default_name}-{user_id[:8]}"),
            "ownerUserId": user_id,
            "createdAt": now,
            "updatedAt": now,
        }
        member = {
            "id": str(uuid.uuid4()),
            "workspaceId": workspace["id"],
            "userId": user_id,
            "role": "owner",
            "createdAt": now,
            "updatedAt": now,
        }
        store["workspaces"].append(workspace)
        store["workspaceMembers"].append(member)
        _hydrate_legacy_workspace_records(store, user_id=user_id, workspace_id=workspace["id"])
        return {**workspace, "currentRole": "owner", "memberCount": 1}

    return await _mutate_store(mutator)


async def create_workspace(user_id: str, name: str) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        workspace = {
            "id": str(uuid.uuid4()),
            "name": name,
            "slug": _slugify(name),
            "ownerUserId": user_id,
            "createdAt": now,
            "updatedAt": now,
        }
        member = {
            "id": str(uuid.uuid4()),
            "workspaceId": workspace["id"],
            "userId": user_id,
            "role": "owner",
            "createdAt": now,
            "updatedAt": now,
        }
        store["workspaces"].append(workspace)
        store["workspaceMembers"].append(member)
        return {**workspace, "currentRole": "owner", "memberCount": 1}

    return await _mutate_store(mutator)


async def list_workspaces_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    memberships = [
        item for item in store["workspaceMembers"] if item["userId"] == user_id
    ]
    items: list[dict[str, Any]] = []
    for membership in memberships:
        workspace = next(
            (
                item
                for item in store["workspaces"]
                if item["id"] == membership["workspaceId"]
            ),
            None,
        )
        if not workspace:
            continue
        items.append(
            {
                **workspace,
                "currentRole": membership["role"],
                "memberCount": len(
                    [
                        item
                        for item in store["workspaceMembers"]
                        if item["workspaceId"] == workspace["id"]
                    ]
                ),
            }
        )
    return _sorted_desc(items)


async def get_workspace_by_id(
    workspace_id: str, user_id: str
) -> dict[str, Any] | None:
    store = await read_store()
    membership = next(
        (
            item
            for item in store["workspaceMembers"]
            if item["workspaceId"] == workspace_id and item["userId"] == user_id
        ),
        None,
    )
    if not membership:
        return None

    workspace = next(
        (item for item in store["workspaces"] if item["id"] == workspace_id),
        None,
    )
    if not workspace:
        return None

    return {
        **workspace,
        "currentRole": membership["role"],
        "memberCount": len(
            [
                item
                for item in store["workspaceMembers"]
                if item["workspaceId"] == workspace_id
            ]
        ),
    }


async def get_user_role_in_workspace(workspace_id: str, user_id: str) -> str | None:
    store = await read_store()
    membership = next(
        (
            item
            for item in store["workspaceMembers"]
            if item["workspaceId"] == workspace_id and item["userId"] == user_id
        ),
        None,
    )
    return membership["role"] if membership else None


async def list_conversations_by_user(
    user_id: str, workspace_id: str | None = None
) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [
            item
            for item in store["conversations"]
            if item["userId"] == user_id and _workspace_match(item, workspace_id)
        ]
    )


async def create_conversation(
    user_id: str,
    title: str = "新的任务",
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        conversation = {
            "id": str(uuid.uuid4()),
            "userId": user_id,
            "workspaceId": workspace_id,
            "title": title,
            "createdAt": now,
            "updatedAt": now,
        }
        store["conversations"].append(conversation)
        return conversation

    return await _mutate_store(mutator)


async def get_conversation_by_id(
    conversation_id: str, user_id: str, workspace_id: str | None = None
) -> dict[str, Any] | None:
    store = await read_store()
    conversation = next(
        (
            item
            for item in store["conversations"]
            if item["id"] == conversation_id
            and item["userId"] == user_id
            and _workspace_match(item, workspace_id)
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
            if item["conversationId"] == conversation_id and _workspace_match(item, workspace_id)
        ],
        "documents": _sorted_desc(
            [
                item
                for item in store["documents"]
                if item["conversationId"] == conversation_id
                and item["userId"] == user_id
                and _workspace_match(item, workspace_id)
            ]
        ),
        "runs": _sorted_desc(
            [
                item
                for item in store["agentRuns"]
                if item["conversationId"] == conversation_id
                and item["userId"] == user_id
                and _workspace_match(item, workspace_id)
            ]
        ),
    }


async def append_message(
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            conversation = next(
                (item for item in store["conversations"] if item["id"] == conversation_id),
                None,
            )
            resolved_workspace_id = conversation.get("workspaceId") if conversation else None

        message = {
            "id": str(uuid.uuid4()),
            "conversationId": conversation_id,
            "userId": user_id,
            "workspaceId": resolved_workspace_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["messages"].append(message)

        for conversation in store["conversations"]:
            if conversation["id"] != conversation_id:
                continue
            conversation["updatedAt"] = now
            if role == "user" and content.strip() and conversation["title"] == "新的任务":
                conversation["title"] = content[:30]
            break

        return message

    return await _mutate_store(mutator)


async def create_agent_run(
    conversation_id: str,
    user_id: str,
    prompt: str,
    metadata: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            conversation = next(
                (item for item in store["conversations"] if item["id"] == conversation_id),
                None,
            )
            resolved_workspace_id = conversation.get("workspaceId") if conversation else None

        run = {
            "id": str(uuid.uuid4()),
            "conversationId": conversation_id,
            "userId": user_id,
            "workspaceId": resolved_workspace_id,
            "prompt": prompt,
            "status": "running",
            "toolCalls": [],
            "memoryContext": "",
            "memorySource": "",
            "result": "",
            "structuredOutput": None,
            "promptVersion": metadata.get("promptVersion") if metadata else None,
            "modelUsage": None,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["agentRuns"].append(run)
        return run

    return await _mutate_store(mutator)


async def update_agent_run(run_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    now = _now()

    def mutator(store: Store) -> dict[str, Any] | None:
        run = next((item for item in store["agentRuns"] if item["id"] == run_id), None)
        if not run:
            return None

        run.update(updates)
        run["updatedAt"] = now
        return run

    return await _mutate_store(mutator)


async def list_agent_runs_by_user(
    user_id: str, workspace_id: str | None = None
) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [
            item
            for item in store["agentRuns"]
            if item["userId"] == user_id and _workspace_match(item, workspace_id)
        ]
    )


async def get_agent_run_by_id(
    run_id: str, user_id: str, workspace_id: str | None = None
) -> dict[str, Any] | None:
    store = await read_store()
    run = next(
        (
            item
            for item in store["agentRuns"]
            if item["id"] == run_id
            and item["userId"] == user_id
            and _workspace_match(item, workspace_id)
        ),
        None,
    )
    if not run:
        return None

    return {
        **run,
        "steps": sorted(
            [item for item in store["agentSteps"] if item["runId"] == run_id],
            key=lambda item: item.get("sequence", 0),
        ),
        "toolLogs": _sorted_desc(
            [item for item in store["toolCallLogs"] if item["runId"] == run_id]
        ),
        "events": sorted(
            [item for item in store["runEvents"] if item["runId"] == run_id],
            key=lambda item: item.get("sequence", 0),
        ),
        "usage": _sorted_desc(
            [item for item in store["modelUsageLogs"] if item["runId"] == run_id]
        ),
    }


async def create_agent_step(
    run_id: str,
    user_id: str,
    conversation_id: str,
    name: str,
    *,
    workspace_id: str | None = None,
    status: str = "running",
    input_data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            run = next((item for item in store["agentRuns"] if item["id"] == run_id), None)
            resolved_workspace_id = run.get("workspaceId") if run else None

        step = {
            "id": str(uuid.uuid4()),
            "runId": run_id,
            "userId": user_id,
            "conversationId": conversation_id,
            "workspaceId": resolved_workspace_id,
            "name": name,
            "sequence": _next_sequence(store["agentSteps"], field="runId", value=run_id),
            "status": status,
            "input": input_data or {},
            "output": None,
            "error": None,
            "metadata": metadata or {},
            "startedAt": now,
            "completedAt": None,
            "createdAt": now,
            "updatedAt": now,
        }
        store["agentSteps"].append(step)
        return step

    return await _mutate_store(mutator)


async def update_agent_step(step_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    now = _now()

    def mutator(store: Store) -> dict[str, Any] | None:
        step = next((item for item in store["agentSteps"] if item["id"] == step_id), None)
        if not step:
            return None

        step.update(updates)
        step["updatedAt"] = now
        if updates.get("status") in {"completed", "failed", "skipped"}:
            step["completedAt"] = now
        return step

    return await _mutate_store(mutator)


async def list_agent_steps_by_run(run_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return sorted(
        [item for item in store["agentSteps"] if item["runId"] == run_id],
        key=lambda item: item.get("sequence", 0),
    )


async def create_run_event(
    run_id: str,
    user_id: str,
    conversation_id: str,
    event_type: str,
    workspace_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            run = next((item for item in store["agentRuns"] if item["id"] == run_id), None)
            resolved_workspace_id = run.get("workspaceId") if run else None

        event = {
            "id": str(uuid.uuid4()),
            "runId": run_id,
            "userId": user_id,
            "conversationId": conversation_id,
            "workspaceId": resolved_workspace_id,
            "type": event_type,
            "sequence": _next_sequence(store["runEvents"], field="runId", value=run_id),
            "payload": payload or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["runEvents"].append(event)
        return event

    return await _mutate_store(mutator)


async def list_run_events_by_run(run_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return sorted(
        [item for item in store["runEvents"] if item["runId"] == run_id],
        key=lambda item: item.get("sequence", 0),
    )


async def create_tool_call_log(
    run_id: str,
    conversation_id: str,
    user_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    workspace_id: str | None = None,
    status: str = "completed",
    result: Any = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            run = next((item for item in store["agentRuns"] if item["id"] == run_id), None)
            resolved_workspace_id = run.get("workspaceId") if run else None

        tool_call = {
            "id": str(uuid.uuid4()),
            "runId": run_id,
            "conversationId": conversation_id,
            "userId": user_id,
            "workspaceId": resolved_workspace_id,
            "name": name,
            "arguments": arguments,
            "status": status,
            "result": result,
            "error": error,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["toolCallLogs"].append(tool_call)
        return tool_call

    return await _mutate_store(mutator)


async def list_tool_call_logs_by_user(
    user_id: str, workspace_id: str | None = None
) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [
            item
            for item in store["toolCallLogs"]
            if item["userId"] == user_id and _workspace_match(item, workspace_id)
        ]
    )


async def list_tool_call_logs_by_run(run_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc([item for item in store["toolCallLogs"] if item["runId"] == run_id])


async def create_document(record: dict[str, Any]) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
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
        return document

    return await _mutate_store(mutator)


async def update_document(
    document_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    now = _now()

    def mutator(store: Store) -> dict[str, Any] | None:
        document = next(
            (item for item in store["documents"] if item["id"] == document_id),
            None,
        )
        if not document:
            return None

        document.update(updates)
        document["updatedAt"] = now
        return document

    return await _mutate_store(mutator)


async def get_document_by_id(
    document_id: str, user_id: str, workspace_id: str | None = None
) -> dict[str, Any] | None:
    store = await read_store()
    return next(
        (
            item
            for item in store["documents"]
            if item["id"] == document_id
            and item["userId"] == user_id
            and _workspace_match(item, workspace_id)
        ),
        None,
    )


async def list_documents_by_user(
    user_id: str, workspace_id: str | None = None
) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [
            item
            for item in store["documents"]
            if item["userId"] == user_id and _workspace_match(item, workspace_id)
        ]
    )


async def create_background_job(record: dict[str, Any]) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        job = {
            "id": str(uuid.uuid4()),
            "status": "queued",
            "output": None,
            "error": None,
            "attemptCount": 0,
            "createdAt": now,
            "updatedAt": now,
            **record,
        }
        store["backgroundJobs"].append(job)
        return job

    return await _mutate_store(mutator)


async def update_background_job(
    job_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    now = _now()

    def mutator(store: Store) -> dict[str, Any] | None:
        job = next((item for item in store["backgroundJobs"] if item["id"] == job_id), None)
        if not job:
            return None

        job.update(updates)
        job["updatedAt"] = now
        return job

    return await _mutate_store(mutator)


async def get_background_job_by_id(
    job_id: str, user_id: str, workspace_id: str | None = None
) -> dict[str, Any] | None:
    store = await read_store()
    return next(
        (
            item
            for item in store["backgroundJobs"]
            if item["id"] == job_id
            and item["userId"] == user_id
            and _workspace_match(item, workspace_id)
        ),
        None,
    )


async def list_background_jobs_by_user(
    user_id: str, workspace_id: str | None = None
) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [
            item
            for item in store["backgroundJobs"]
            if item["userId"] == user_id and _workspace_match(item, workspace_id)
        ]
    )


async def upsert_preference(
    user_id: str, key: str, value: str, source: str = "app"
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
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
            return existing

        preference = {
            "id": str(uuid.uuid4()),
            "userId": user_id,
            "key": key,
            "value": value,
            "source": source,
            "createdAt": now,
            "updatedAt": now,
        }
        store["userPreferences"].append(preference)
        return preference

    return await _mutate_store(mutator)


async def delete_preference(preference_id: str, user_id: str) -> bool:
    def mutator(store: Store) -> bool:
        before = len(store["userPreferences"])
        store["userPreferences"] = [
            item
            for item in store["userPreferences"]
            if not (item["id"] == preference_id and item["userId"] == user_id)
        ]
        return len(store["userPreferences"]) != before

    return bool(await _mutate_store(mutator))


async def list_preferences_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [item for item in store["userPreferences"] if item["userId"] == user_id]
    )


async def create_memory_writeback(
    user_id: str,
    *,
    conversation_id: str | None,
    source: str,
    summary: str,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        writeback = {
            "id": str(uuid.uuid4()),
            "userId": user_id,
            "conversationId": conversation_id,
            "source": source,
            "summary": _trim_text(summary, limit=2000),
            "items": items or [],
            "createdAt": now,
            "updatedAt": now,
        }
        store["memoryWritebacks"].append(writeback)
        return writeback

    return await _mutate_store(mutator)


async def list_memory_writebacks_by_user(user_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [item for item in store["memoryWritebacks"] if item["userId"] == user_id]
    )


async def create_audit_log(
    user_id: str,
    action: str,
    resource_type: str,
    *,
    workspace_id: str | None = None,
    resource_id: str | None = None,
    status: str = "success",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        record = {
            "id": str(uuid.uuid4()),
            "userId": user_id,
            "workspaceId": workspace_id,
            "action": action,
            "resourceType": resource_type,
            "resourceId": resource_id,
            "status": status,
            "detail": detail or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["auditLogs"].append(record)
        return record

    return await _mutate_store(mutator)


async def list_audit_logs_by_user(
    user_id: str, *, workspace_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc(
        [
            item
            for item in store["auditLogs"]
            if item["userId"] == user_id and _workspace_match(item, workspace_id)
        ]
    )[:limit]


async def create_model_usage_log(
    run_id: str,
    user_id: str,
    conversation_id: str,
    *,
    model: str,
    provider: str,
    stage: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    estimated_cost: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        usage = {
            "id": str(uuid.uuid4()),
            "runId": run_id,
            "userId": user_id,
            "conversationId": conversation_id,
            "model": model,
            "provider": provider,
            "stage": stage,
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
            "estimatedCost": estimated_cost,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["modelUsageLogs"].append(usage)
        return usage

    return await _mutate_store(mutator)


async def list_model_usage_logs_by_run(run_id: str) -> list[dict[str, Any]]:
    store = await read_store()
    return _sorted_desc([item for item in store["modelUsageLogs"] if item["runId"] == run_id])


async def record_rate_limit_event(
    user_id: str,
    scope: str,
    *,
    allowed: bool,
    limit: int,
    window_seconds: int,
    count_in_window: int,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "userId": user_id,
            "scope": scope,
            "allowed": allowed,
            "limit": limit,
            "windowSeconds": window_seconds,
            "countInWindow": count_in_window,
            "createdAt": now,
            "updatedAt": now,
        }
        store["rateLimitEvents"].append(event)
        return event

    return await _mutate_store(mutator)


async def check_rate_limit(
    user_id: str,
    scope: str,
    *,
    limit: int,
    window_seconds: int,
) -> dict[str, Any]:
    store = await read_store()
    threshold = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    recent = [
        item
        for item in store["rateLimitEvents"]
        if item.get("userId") == user_id
        and item.get("scope") == scope
        and item.get("allowed") is True
        and _dt(item.get("createdAt")) >= threshold
    ]

    count_in_window = len(recent)
    allowed = count_in_window < limit
    await record_rate_limit_event(
        user_id,
        scope,
        allowed=allowed,
        limit=limit,
        window_seconds=window_seconds,
        count_in_window=count_in_window + (1 if allowed else 0),
    )
    return {
        "allowed": allowed,
        "countInWindow": count_in_window,
        "remaining": max(limit - count_in_window - (1 if allowed else 0), 0),
        "retryAfterSeconds": 0 if allowed else window_seconds,
    }


async def upsert_prompt_version(
    key: str,
    version: str,
    content: str,
    *,
    is_active: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        existing = next(
            (
                item
                for item in store["promptVersions"]
                if item["key"] == key and item["version"] == version
            ),
            None,
        )
        if existing:
            existing.update(
                {
                    "content": content,
                    "isActive": is_active,
                    "metadata": metadata or existing.get("metadata", {}),
                    "updatedAt": now,
                }
            )
            return existing

        prompt_version = {
            "id": str(uuid.uuid4()),
            "key": key,
            "version": version,
            "content": content,
            "isActive": is_active,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["promptVersions"].append(prompt_version)
        return prompt_version

    return await _mutate_store(mutator)


async def get_prompt_version(key: str, version: str | None = None) -> dict[str, Any] | None:
    store = await read_store()
    matches = [item for item in store["promptVersions"] if item["key"] == key]
    if version is not None:
        return next((item for item in matches if item["version"] == version), None)

    active_matches = [item for item in matches if item.get("isActive")]
    if active_matches:
        return _sorted_desc(active_matches)[0]
    return _sorted_desc(matches)[0] if matches else None


async def list_prompt_versions(key: str | None = None) -> list[dict[str, Any]]:
    store = await read_store()
    items = store["promptVersions"]
    if key is not None:
        items = [item for item in items if item["key"] == key]
    return _sorted_desc(items)


async def upsert_mcp_server(
    name: str,
    *,
    transport: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        existing = next((item for item in store["mcpServers"] if item["name"] == name), None)
        if existing:
            existing.update(
                {
                    "transport": transport,
                    "status": status,
                    "metadata": metadata or existing.get("metadata", {}),
                    "updatedAt": now,
                }
            )
            return existing

        record = {
            "id": str(uuid.uuid4()),
            "name": name,
            "transport": transport,
            "status": status,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["mcpServers"].append(record)
        return record

    return await _mutate_store(mutator)


async def upsert_mcp_server_connection(
    server_name: str,
    *,
    user_id: str | None,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()

    def mutator(store: Store) -> dict[str, Any]:
        existing = next(
            (
                item
                for item in store["mcpServerConnections"]
                if item["serverName"] == server_name and item.get("userId") == user_id
            ),
            None,
        )
        if existing:
            existing.update(
                {
                    "status": status,
                    "metadata": metadata or existing.get("metadata", {}),
                    "updatedAt": now,
                }
            )
            return existing

        record = {
            "id": str(uuid.uuid4()),
            "serverName": server_name,
            "userId": user_id,
            "status": status,
            "metadata": metadata or {},
            "createdAt": now,
            "updatedAt": now,
        }
        store["mcpServerConnections"].append(record)
        return record

    return await _mutate_store(mutator)


async def sync_tool_registry(
    server_name: str, tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    now = _now()

    def mutator(store: Store) -> list[dict[str, Any]]:
        seen_names: set[str] = set()
        synced: list[dict[str, Any]] = []

        for tool in tools:
            tool_name = str(tool.get("name") or "").strip()
            if not tool_name:
                continue
            seen_names.add(tool_name)

            existing = next(
                (item for item in store["toolRegistry"] if item["name"] == tool_name),
                None,
            )
            payload = {
                "serverName": server_name,
                "name": tool_name,
                "description": tool.get("description", ""),
                "inputSchema": tool.get("inputSchema") or {},
                "status": "available",
                "updatedAt": now,
            }
            if existing:
                existing.update(payload)
                synced.append(existing)
                continue

            record = {
                "id": str(uuid.uuid4()),
                **payload,
                "createdAt": now,
            }
            store["toolRegistry"].append(record)
            synced.append(record)

        for item in store["toolRegistry"]:
            if item.get("serverName") == server_name and item.get("name") not in seen_names:
                item["status"] = "inactive"
                item["updatedAt"] = now

        return synced

    return await _mutate_store(mutator)


async def list_tool_registry() -> list[dict[str, Any]]:
    store = await read_store()
    return sorted(
        store["toolRegistry"],
        key=lambda item: (item.get("serverName") or "", item.get("name") or ""),
    )
