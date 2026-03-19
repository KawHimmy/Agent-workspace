from __future__ import annotations

import asyncio
import secrets
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from .agent import run_agent_task
from .auth_proxy import get_current_user, proxy_auth_request, require_user
from .config import settings
from .documents import process_document_summary
from .mcp_client import list_mcp_tools
from .memory import write_conversation_memory
from .store import (
    append_message,
    create_agent_run,
    create_background_job,
    create_conversation,
    create_document,
    get_conversation_by_id,
    get_document_by_id,
    list_background_jobs_by_user,
    list_conversations_by_user,
    list_documents_by_user,
    list_preferences_by_user,
    update_agent_run,
    update_background_job,
    update_document,
)

TRIGGER_SUMMARY_ROUTE = "/internal/trigger/document-summary"
PROCESS_SUMMARY_ROUTE = "/internal/documents/process-summary"

app = FastAPI(title="ReAct Agent Workspace Python API")


def json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def require_internal_secret(request: Request) -> None:
    provided_secret = request.headers.get("x-internal-secret", "")
    if not secrets.compare_digest(provided_secret, settings.internal_service_secret):
        raise HTTPException(status_code=403, detail="Forbidden")


async def background_document_summary(document_id: str, user_id: str, job_id: str) -> None:
    try:
        await process_document_summary(document_id, user_id, job_id)
    except Exception as exc:
        message = str(exc)
        await update_document(document_id, {"status": "failed", "summary": message})
        await update_background_job(job_id, {"status": "failed", "error": message})


async def trigger_document_summary_job(
    document_id: str, user_id: str, job_id: str
) -> str:
    payload = {
        "documentId": document_id,
        "userId": user_id,
        "jobId": job_id,
        "callbackUrl": f"{settings.app_url}{PROCESS_SUMMARY_ROUTE}",
        "internalSecret": settings.internal_service_secret,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{settings.auth_service_url}{TRIGGER_SUMMARY_ROUTE}",
            json=payload,
            headers={"x-internal-secret": settings.internal_service_secret},
        )

    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise RuntimeError(f"Trigger.dev dispatch failed: {detail}")

    return "trigger.dev"


@app.post(PROCESS_SUMMARY_ROUTE)
async def internal_process_document_summary(request: Request) -> Response:
    require_internal_secret(request)
    payload = await request.json()

    document_id = str(payload.get("documentId") or "").strip()
    user_id = str(payload.get("userId") or "").strip()
    job_id = str(payload.get("jobId") or "").strip()
    if not document_id or not user_id or not job_id:
        return json_error("documentId, userId and jobId are required.", status_code=400)

    try:
        summary = await process_document_summary(document_id, user_id, job_id)
    except Exception as exc:
        message = str(exc)
        await update_document(document_id, {"status": "failed", "summary": message})
        await update_background_job(job_id, {"status": "failed", "error": message})
        return json_error(message, status_code=500)

    return JSONResponse({"ok": True, "summary": summary})


@app.api_route(
    "/api/auth/{auth_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def auth_proxy_route(auth_path: str, request: Request) -> Response:
    return await proxy_auth_request(request, auth_path)


@app.get("/api/me")
async def api_me(request: Request) -> Response:
    user = await get_current_user(request)
    return JSONResponse(user)


@app.get("/api/bootstrap")
async def api_bootstrap(request: Request) -> Response:
    user = await require_user(request)
    conversations, documents, jobs, preferences = await asyncio.gather(
        list_conversations_by_user(user["id"]),
        list_documents_by_user(user["id"]),
        list_background_jobs_by_user(user["id"]),
        list_preferences_by_user(user["id"]),
    )
    return JSONResponse(
        {
            "user": user,
            "conversations": conversations,
            "documents": documents,
            "jobs": jobs,
            "preferences": preferences,
        }
    )


@app.get("/api/tools")
async def api_tools() -> Response:
    return JSONResponse(await list_mcp_tools())


@app.get("/api/conversations")
async def api_list_conversations(request: Request) -> Response:
    user = await require_user(request)
    return JSONResponse(await list_conversations_by_user(user["id"]))


@app.post("/api/conversations")
async def api_create_conversation(request: Request) -> Response:
    user = await require_user(request)
    payload = await request.json()
    title = str(payload.get("title") or "新的任务").strip() or "新的任务"
    conversation = await create_conversation(user["id"], title)
    return JSONResponse(conversation, status_code=201)


@app.get("/api/conversations/{conversation_id}")
async def api_get_conversation(conversation_id: str, request: Request) -> Response:
    user = await require_user(request)
    conversation = await get_conversation_by_id(conversation_id, user["id"])
    if not conversation:
        raise HTTPException(status_code=404, detail="未找到会话。")
    return JSONResponse(conversation)


@app.post("/api/agent/run")
async def api_agent_run(request: Request) -> Response:
    user = await require_user(request)
    payload = await request.json()
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return json_error("prompt 不能为空。")

    conversation_id = payload.get("conversationId")
    if not conversation_id:
        conversation = await create_conversation(user["id"], prompt[:30] or "新的任务")
        conversation_id = conversation["id"]

    await append_message(conversation_id, user["id"], "user", prompt)
    run = await create_agent_run(conversation_id, user["id"], prompt)

    try:
        conversation = await get_conversation_by_id(conversation_id, user["id"])
        agent_result = await run_agent_task(
            user_id=user["id"],
            conversation_id=conversation_id,
            prompt=prompt,
            history=conversation["messages"] if conversation else [],
        )
        assistant_message = await append_message(
            conversation_id,
            user["id"],
            "assistant",
            agent_result["answer"],
            {
                "toolCalls": agent_result["toolCalls"],
                "memorySource": agent_result["memorySource"],
            },
        )
        await write_conversation_memory(user["id"], prompt, agent_result["answer"])
        completed_run = await update_agent_run(
            run["id"],
            {
                "status": "completed",
                "result": agent_result["answer"],
                "toolCalls": agent_result["toolCalls"],
                "memoryContext": agent_result["memoryContext"],
            },
        )
        return JSONResponse(
            {
                "conversationId": conversation_id,
                "message": assistant_message,
                "run": completed_run,
            }
        )
    except Exception as exc:
        await update_agent_run(run["id"], {"status": "failed", "result": str(exc)})
        return json_error(str(exc), status_code=500)


@app.post("/api/documents/upload")
async def api_upload_document(request: Request, document: UploadFile = File(...)) -> Response:
    user = await require_user(request)
    form = await request.form()
    conversation_id = form.get("conversationId")
    if not conversation_id:
        conversation = await create_conversation(user["id"], "文档分析")
        conversation_id = conversation["id"]

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    original_name = document.filename or "uploaded-file"
    suffix = Path(original_name).suffix
    file_path = settings.uploads_dir / f"{uuid.uuid4()}{suffix}"
    content = await document.read()
    file_path.write_bytes(content)

    document_record = await create_document(
        {
            "userId": user["id"],
            "conversationId": conversation_id,
            "originalName": original_name,
            "contentType": document.content_type,
            "size": len(content),
            "filePath": str(file_path),
        }
    )
    job = await create_background_job(
        {
            "userId": user["id"],
            "conversationId": conversation_id,
            "documentId": document_record["id"],
            "type": "document-summary",
        }
    )

    queue_provider = "python-background"
    queue_fallback_reason = None
    try:
        # Prefer Trigger.dev for async orchestration when the worker is available.
        queue_provider = await trigger_document_summary_job(
            document_record["id"], user["id"], job["id"]
        )
    except Exception as exc:
        queue_fallback_reason = str(exc)
        # Keep the upload flow usable even if Trigger.dev is not running locally yet.
        asyncio.create_task(
            background_document_summary(document_record["id"], user["id"], job["id"])
        )

    return JSONResponse(
        {
            "conversationId": conversation_id,
            "document": document_record,
            "job": {
                **job,
                "queueProvider": queue_provider,
                "queueFallbackReason": queue_fallback_reason,
            },
        },
        status_code=202,
    )


@app.get("/api/documents/{document_id}")
async def api_get_document(document_id: str, request: Request) -> Response:
    user = await require_user(request)
    document = await get_document_by_id(document_id, user["id"])
    if not document:
        raise HTTPException(status_code=404, detail="未找到文档。")
    return JSONResponse(document)


@app.get("/api/jobs")
async def api_jobs(request: Request) -> Response:
    user = await require_user(request)
    return JSONResponse(await list_background_jobs_by_user(user["id"]))


@app.get("/")
async def root() -> Response:
    return FileResponse(
        settings.web_dir / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/{file_path:path}")
async def static_files(file_path: str) -> Response:
    if file_path.startswith("api/") or file_path.startswith("internal/"):
        raise HTTPException(status_code=404, detail="Not found")

    candidate = settings.web_dir / file_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate, headers={"Cache-Control": "no-store, max-age=0"})

    return FileResponse(
        settings.web_dir / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
