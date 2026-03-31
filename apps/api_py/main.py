from __future__ import annotations

import asyncio
import secrets
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from .agent import PROMPT_VERSION, run_agent_task
from .auth_proxy import get_current_user, proxy_auth_request, require_user
from .config import settings
from .documents import process_document_summary
from .mcp_client import list_mcp_tools, test_mcp_tool
from .memory import retrieve_memory_context, write_conversation_memory
from .store import (
    append_message,
    check_rate_limit,
    create_agent_run,
    create_audit_log,
    create_background_job,
    create_conversation,
    create_document,
    create_workspace,
    delete_preference,
    ensure_default_workspace_for_user,
    get_agent_run_by_id,
    get_background_job_by_id,
    get_conversation_by_id,
    get_document_by_id,
    get_workspace_by_id,
    list_agent_runs_by_user,
    list_background_jobs_by_user,
    list_conversations_by_user,
    list_documents_by_user,
    list_memory_writebacks_by_user,
    list_preferences_by_user,
    list_run_events_by_run,
    list_tool_call_logs_by_run,
    list_tool_call_logs_by_user,
    list_tool_registry,
    list_model_usage_logs_by_run,
    list_workspaces_by_user,
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


async def require_workspace_context(
    request: Request,
    user: dict,
    *,
    minimum_role: str = "viewer",
) -> dict:
    default_workspace = await ensure_default_workspace_for_user(
        user["id"],
        workspace_name=f"{(user.get('name') or 'My').strip() or 'My'} Workspace",
    )
    workspace_id = request.headers.get("x-workspace-id") or default_workspace["id"]
    workspace = await get_workspace_by_id(workspace_id, user["id"])
    if not workspace:
        raise HTTPException(status_code=403, detail="当前工作区不可访问。")

    role_rank = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
    current_rank = role_rank.get(workspace.get("currentRole") or "viewer", 0)
    required_rank = role_rank.get(minimum_role, 0)
    if current_rank < required_rank:
        raise HTTPException(status_code=403, detail="当前工作区权限不足。")

    return workspace


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


async def enqueue_document_summary_job(
    *, document_id: str, user_id: str, job_id: str
) -> dict[str, str | None]:
    queue_provider = "python-background"
    queue_fallback_reason = None

    try:
        queue_provider = await trigger_document_summary_job(document_id, user_id, job_id)
    except Exception as exc:
        queue_fallback_reason = str(exc)
        asyncio.create_task(background_document_summary(document_id, user_id, job_id))

    return {
        "queueProvider": queue_provider,
        "queueFallbackReason": queue_fallback_reason,
    }


async def ensure_tool_catalog_for_user(user_id: str) -> list[dict]:
    registry = await list_tool_registry()
    if registry:
        return registry

    try:
        await list_mcp_tools(user_id)
    except Exception:
        return []
    return await list_tool_registry()


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


@app.get("/api/workspaces")
async def api_workspaces(request: Request) -> Response:
    user = await require_user(request)
    await ensure_default_workspace_for_user(
        user["id"], workspace_name=f"{(user.get('name') or 'My').strip() or 'My'} Workspace"
    )
    workspaces = await list_workspaces_by_user(user["id"])
    return JSONResponse({"workspaces": workspaces})


@app.post("/api/workspaces")
async def api_create_workspace(request: Request) -> Response:
    user = await require_user(request)
    payload = await request.json()
    name = str(payload.get("name") or "").strip() or "New Workspace"
    workspace = await create_workspace(user["id"], name)
    await create_audit_log(
        user["id"],
        "workspace.create",
        "workspace",
        workspace_id=workspace["id"],
        resource_id=workspace["id"],
        status="success",
    )
    return JSONResponse(workspace, status_code=201)


@app.get("/api/bootstrap")
async def api_bootstrap(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    (
        conversations,
        documents,
        jobs,
        preferences,
        runs,
        writebacks,
        tool_logs,
        tool_registry,
        workspaces,
    ) = await asyncio.gather(
        list_conversations_by_user(user["id"], workspace["id"]),
        list_documents_by_user(user["id"], workspace["id"]),
        list_background_jobs_by_user(user["id"], workspace["id"]),
        list_preferences_by_user(user["id"]),
        list_agent_runs_by_user(user["id"], workspace["id"]),
        list_memory_writebacks_by_user(user["id"]),
        list_tool_call_logs_by_user(user["id"], workspace["id"]),
        ensure_tool_catalog_for_user(user["id"]),
        list_workspaces_by_user(user["id"]),
    )
    return JSONResponse(
        {
            "user": user,
            "currentWorkspace": workspace,
            "workspaces": workspaces,
            "conversations": conversations,
            "documents": documents,
            "jobs": jobs,
            "preferences": preferences,
            "runs": runs,
            "memoryWritebacks": writebacks,
            "toolLogs": tool_logs,
            "tools": tool_registry,
        }
    )


@app.get("/api/tools")
async def api_tools(request: Request) -> Response:
    user = await require_user(request)
    await require_workspace_context(request, user)
    tools = await list_mcp_tools(user["id"])
    return JSONResponse({"tools": tools, "registry": await list_tool_registry()})


@app.post("/api/tools/connect")
async def api_connect_tools(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    tools = await list_mcp_tools(user["id"])
    await create_audit_log(
        user["id"],
        "tools.connect",
        "mcp-server",
        workspace_id=workspace["id"],
        status="success",
        detail={"toolCount": len(tools)},
    )
    return JSONResponse({"connected": True, "tools": tools})


@app.post("/api/tools/test")
async def api_test_tool(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    payload = await request.json()
    tool_name = str(payload.get("name") or "").strip()
    arguments = payload.get("arguments") or {}
    if not tool_name:
        return json_error("name 不能为空。")

    result = await test_mcp_tool(tool_name, arguments)
    await create_audit_log(
        user["id"],
        "tools.test",
        "tool",
        workspace_id=workspace["id"],
        resource_id=tool_name,
        status="success",
        detail={"arguments": arguments},
    )
    return JSONResponse(result)


@app.get("/api/tools/logs")
async def api_tool_logs(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    return JSONResponse(await list_tool_call_logs_by_user(user["id"], workspace["id"]))


@app.get("/api/conversations")
async def api_list_conversations(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    return JSONResponse(await list_conversations_by_user(user["id"], workspace["id"]))


@app.post("/api/conversations")
async def api_create_conversation(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    payload = await request.json()
    title = str(payload.get("title") or "新的任务").strip() or "新的任务"
    conversation = await create_conversation(user["id"], title, workspace_id=workspace["id"])
    await create_audit_log(
        user["id"],
        "conversation.create",
        "conversation",
        workspace_id=workspace["id"],
        resource_id=conversation["id"],
    )
    return JSONResponse(conversation, status_code=201)


@app.get("/api/conversations/{conversation_id}")
async def api_get_conversation(conversation_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    conversation = await get_conversation_by_id(conversation_id, user["id"], workspace["id"])
    if not conversation:
        raise HTTPException(status_code=404, detail="未找到会话。")
    return JSONResponse(conversation)


@app.get("/api/agent/runs")
async def api_agent_runs(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    return JSONResponse(await list_agent_runs_by_user(user["id"], workspace["id"]))


@app.get("/api/agent/runs/{run_id}")
async def api_agent_run_detail(run_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    run = await get_agent_run_by_id(run_id, user["id"], workspace["id"])
    if not run:
        raise HTTPException(status_code=404, detail="未找到运行记录。")
    return JSONResponse(run)


@app.get("/api/agent/runs/{run_id}/trace")
async def api_agent_run_trace(run_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    run = await get_agent_run_by_id(run_id, user["id"], workspace["id"])
    if not run:
        raise HTTPException(status_code=404, detail="未找到运行记录。")
    return JSONResponse(
        {
            "runId": run_id,
            "steps": run["steps"],
            "events": await list_run_events_by_run(run_id),
            "toolLogs": await list_tool_call_logs_by_run(run_id),
            "usage": await list_model_usage_logs_by_run(run_id),
        }
    )


@app.post("/api/agent/run")
async def api_agent_run(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    payload = await request.json()
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return json_error("prompt 不能为空。")

    rate_limit = await check_rate_limit(
        user["id"],
        "agent.run",
        limit=settings.agent_rate_limit_count,
        window_seconds=settings.agent_rate_limit_window_seconds,
    )
    if not rate_limit["allowed"]:
        return json_error(
            f"请求太频繁，请在 {rate_limit['retryAfterSeconds']} 秒后再试。",
            status_code=429,
        )

    conversation_id = payload.get("conversationId")
    if not conversation_id:
        conversation = await create_conversation(
            user["id"], prompt[:30] or "新的任务", workspace_id=workspace["id"]
        )
        conversation_id = conversation["id"]
    else:
        existing_conversation = await get_conversation_by_id(
            conversation_id, user["id"], workspace["id"]
        )
        if not existing_conversation:
            raise HTTPException(status_code=404, detail="未找到当前工作区下的会话。")

    await append_message(
        conversation_id, user["id"], "user", prompt, workspace_id=workspace["id"]
    )
    run = await create_agent_run(
        conversation_id,
        user["id"],
        prompt,
        metadata={"promptVersion": PROMPT_VERSION},
        workspace_id=workspace["id"],
    )

    try:
        conversation = await get_conversation_by_id(conversation_id, user["id"], workspace["id"])
        agent_result = await run_agent_task(
            run_id=run["id"],
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
                "promptVersion": agent_result["promptVersion"],
                "structuredOutput": agent_result["structuredOutput"],
                "modelUsage": agent_result["modelUsage"],
            },
            workspace_id=workspace["id"],
        )
        await write_conversation_memory(
            user["id"],
            conversation_id,
            prompt,
            agent_result["answer"],
        )
        completed_run = await update_agent_run(
            run["id"],
            {
                "status": "completed",
                "result": agent_result["answer"],
                "toolCalls": agent_result["toolCalls"],
                "memoryContext": agent_result["memoryContext"],
                "memorySource": agent_result["memorySource"],
                "structuredOutput": agent_result["structuredOutput"],
                "promptVersion": agent_result["promptVersion"],
                "modelUsage": agent_result["modelUsage"],
            },
        )
        await create_audit_log(
            user["id"],
            "agent.run",
            "agent-run",
            workspace_id=workspace["id"],
            resource_id=run["id"],
            status="success",
            detail={"conversationId": conversation_id},
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
        await create_audit_log(
            user["id"],
            "agent.run",
            "agent-run",
            workspace_id=workspace["id"],
            resource_id=run["id"],
            status="failed",
            detail={"error": str(exc)},
        )
        return json_error(str(exc), status_code=500)


@app.get("/api/memory")
async def api_memory(request: Request) -> Response:
    user = await require_user(request)
    await require_workspace_context(request, user)
    preferences, writebacks = await asyncio.gather(
        list_preferences_by_user(user["id"]),
        list_memory_writebacks_by_user(user["id"]),
    )
    return JSONResponse({"preferences": preferences, "writebacks": writebacks})


@app.post("/api/memory/refresh")
async def api_memory_refresh(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    payload = await request.json()
    query = str(payload.get("query") or "总结当前长期记忆").strip()
    memory = await retrieve_memory_context(user["id"], query)
    await create_audit_log(
        user["id"],
        "memory.refresh",
        "memory",
        workspace_id=workspace["id"],
        status="success",
        detail={"query": query, "source": memory["source"]},
    )
    return JSONResponse(memory)


@app.delete("/api/memory/{memory_id}")
async def api_memory_delete(memory_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    deleted = await delete_preference(memory_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到记忆项。")
    await create_audit_log(
        user["id"],
        "memory.delete",
        "memory",
        workspace_id=workspace["id"],
        resource_id=memory_id,
        status="success",
    )
    return JSONResponse({"deleted": True})


@app.post("/api/documents/upload")
async def api_upload_document(request: Request, document: UploadFile = File(...)) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    form = await request.form()
    conversation_id = form.get("conversationId")
    if not conversation_id:
        conversation = await create_conversation(
            user["id"], "文档分析", workspace_id=workspace["id"]
        )
        conversation_id = conversation["id"]
    else:
        existing_conversation = await get_conversation_by_id(
            str(conversation_id), user["id"], workspace["id"]
        )
        if not existing_conversation:
            raise HTTPException(status_code=404, detail="未找到当前工作区下的会话。")

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    original_name = document.filename or "uploaded-file"
    suffix = Path(original_name).suffix
    file_path = settings.uploads_dir / f"{uuid.uuid4()}{suffix}"
    content = await document.read()
    file_path.write_bytes(content)

    document_record = await create_document(
        {
            "userId": user["id"],
            "workspaceId": workspace["id"],
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
            "workspaceId": workspace["id"],
            "conversationId": conversation_id,
            "documentId": document_record["id"],
            "type": "document-summary",
        }
    )
    queue_info = await enqueue_document_summary_job(
        document_id=document_record["id"],
        user_id=user["id"],
        job_id=job["id"],
    )

    await create_audit_log(
        user["id"],
        "document.upload",
        "document",
        workspace_id=workspace["id"],
        resource_id=document_record["id"],
        status="success",
        detail={"conversationId": conversation_id, **queue_info},
    )

    return JSONResponse(
        {
            "conversationId": conversation_id,
            "document": document_record,
            "job": {**job, **queue_info},
        },
        status_code=202,
    )


@app.get("/api/documents/{document_id}")
async def api_get_document(document_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    document = await get_document_by_id(document_id, user["id"], workspace["id"])
    if not document:
        raise HTTPException(status_code=404, detail="未找到文档。")
    return JSONResponse(document)


@app.post("/api/documents/{document_id}/process")
async def api_process_document(document_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    document = await get_document_by_id(document_id, user["id"], workspace["id"])
    if not document:
        raise HTTPException(status_code=404, detail="未找到文档。")

    await update_document(document_id, {"status": "queued", "summary": ""})
    job = await create_background_job(
        {
            "userId": user["id"],
            "workspaceId": workspace["id"],
            "conversationId": document["conversationId"],
            "documentId": document_id,
            "type": "document-summary",
            "attemptCount": 1,
        }
    )
    queue_info = await enqueue_document_summary_job(
        document_id=document_id,
        user_id=user["id"],
        job_id=job["id"],
    )

    await create_audit_log(
        user["id"],
        "document.process",
        "document",
        workspace_id=workspace["id"],
        resource_id=document_id,
        status="success",
        detail=queue_info,
    )
    return JSONResponse({"documentId": document_id, "job": {**job, **queue_info}})


@app.get("/api/jobs")
async def api_jobs(request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    return JSONResponse(await list_background_jobs_by_user(user["id"], workspace["id"]))


@app.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user)
    job = await get_background_job_by_id(job_id, user["id"], workspace["id"])
    if not job:
        raise HTTPException(status_code=404, detail="未找到后台任务。")
    return JSONResponse(job)


@app.post("/api/jobs/{job_id}/retry")
async def api_retry_job(job_id: str, request: Request) -> Response:
    user = await require_user(request)
    workspace = await require_workspace_context(request, user, minimum_role="member")
    job = await get_background_job_by_id(job_id, user["id"], workspace["id"])
    if not job:
        raise HTTPException(status_code=404, detail="未找到后台任务。")
    if job.get("type") != "document-summary" or not job.get("documentId"):
        return json_error("当前只支持重试文档摘要任务。", status_code=400)

    next_attempt = int(job.get("attemptCount") or 0) + 1
    await update_background_job(
        job_id,
        {"status": "queued", "error": None, "attemptCount": next_attempt},
    )
    queue_info = await enqueue_document_summary_job(
        document_id=job["documentId"],
        user_id=user["id"],
        job_id=job_id,
    )
    await create_audit_log(
        user["id"],
        "job.retry",
        "background-job",
        workspace_id=workspace["id"],
        resource_id=job_id,
        status="success",
        detail={"attemptCount": next_attempt, **queue_info},
    )
    return JSONResponse({"retried": True, "jobId": job_id, **queue_info})


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
