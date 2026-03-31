from __future__ import annotations

import json
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import settings
from .llm import get_model
from .mcp_client import call_mcp_tool, extract_mcp_text, mcp_result_to_json
from .memory import retrieve_memory_context
from .store import (
    create_agent_step,
    create_model_usage_log,
    create_run_event,
    create_tool_call_log,
    get_prompt_version,
    upsert_prompt_version,
    update_agent_step,
)

PROMPT_KEY = "react-agent-system"
PROMPT_VERSION = "2026-03-31.stage2"
MAX_TOOL_ROUNDS = 3

SYSTEM_PROMPT_TEMPLATE = """
你是 ReAct Agent Workspace 的任务代理。

执行原则：
1. 默认用中文回答，优先准确、具体、可执行。
2. 如果任务涉及上传文档、GitHub 仓库、模板/技术路线，请优先使用工具。
3. 工具调用要克制，先想清楚再行动，不要无意义循环。
4. 回答应显式结合长期记忆和工具观察，不要编造工具未返回的信息。
5. 当信息不足时，直接说明缺口和下一步建议。

当前提示词版本：{prompt_version}
当前任务意图：{intent_text}
当前执行计划：
{plan_text}

可用工具：
{tool_text}

长期记忆：
{memory_text}
""".strip()


class AgentState(TypedDict):
    run_id: str
    user_id: str
    conversation_id: str
    prompt: str
    messages: Annotated[list[Any], add_messages]
    memory_context: str
    memory_source: str
    context_summary: str
    intent: list[str]
    plan: list[str]
    selected_tools: list[str]
    tool_rounds: int
    latest_observation: str
    structured_output: dict[str, Any] | None
    final_answer: str
    prompt_version: str
    model_usage: dict[str, Any]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_content_to_text(item) for item in content)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _serialize_message(message: Any) -> dict[str, Any]:
    if isinstance(message, HumanMessage):
        return {"type": "human", "content": _content_to_text(message.content)}
    if isinstance(message, AIMessage):
        return {
            "type": "ai",
            "content": _content_to_text(message.content),
            "toolCalls": getattr(message, "tool_calls", None) or [],
        }
    if isinstance(message, ToolMessage):
        return {
            "type": "tool",
            "name": getattr(message, "name", ""),
            "content": _content_to_text(message.content),
            "toolCallId": getattr(message, "tool_call_id", None),
        }
    if isinstance(message, SystemMessage):
        return {"type": "system", "content": _content_to_text(message.content)}
    return {"type": type(message).__name__, "content": _content_to_text(message)}


def _serialize_for_store(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_serialize_for_store(item) for item in value[:10]]
    if isinstance(value, dict):
        return {str(key): _serialize_for_store(item) for key, item in list(value.items())[:20]}
    if isinstance(value, (HumanMessage, AIMessage, ToolMessage, SystemMessage)):
        return _serialize_message(value)
    return _content_to_text(value)


def _extract_usage(message: AIMessage) -> dict[str, Any]:
    usage = getattr(message, "usage_metadata", None) or {}
    response_metadata = getattr(message, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}

    prompt_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or token_usage.get("prompt_tokens")
        or token_usage.get("input_tokens")
        or 0
    )
    completion_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or token_usage.get("completion_tokens")
        or token_usage.get("output_tokens")
        or 0
    )
    total_tokens = int(
        usage.get("total_tokens")
        or token_usage.get("total_tokens")
        or prompt_tokens + completion_tokens
    )

    estimated_cost = (
        prompt_tokens / 1_000_000 * settings.glm_input_price_per_1m
        + completion_tokens / 1_000_000 * settings.glm_output_price_per_1m
    )

    return {
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens,
        "estimatedCost": round(estimated_cost, 8),
        "model": response_metadata.get("model_name") or settings.glm_model,
        "provider": response_metadata.get("provider_name") or "glm-compatible",
    }


def _merge_usage(current: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    return {
        "promptTokens": int(current.get("promptTokens", 0)) + int(delta.get("promptTokens", 0)),
        "completionTokens": int(current.get("completionTokens", 0))
        + int(delta.get("completionTokens", 0)),
        "totalTokens": int(current.get("totalTokens", 0)) + int(delta.get("totalTokens", 0)),
        "estimatedCost": round(
            float(current.get("estimatedCost", 0.0)) + float(delta.get("estimatedCost", 0.0)),
            8,
        ),
        "model": delta.get("model") or current.get("model") or settings.glm_model,
        "provider": delta.get("provider") or current.get("provider") or "glm-compatible",
    }


def _heuristic_intents(prompt: str, history: list[dict[str, Any]]) -> list[str]:
    text = "\n".join(
        [prompt] + [str(item.get("content") or "") for item in history[-6:]]
    ).lower()
    intents: list[str] = []

    if any(keyword in text for keyword in ("pdf", "markdown", ".md", "文档", "论文", "附件")):
        intents.append("document_analysis")
    if "github.com/" in text or any(
        keyword in text for keyword in ("仓库", "repo", "repository", "issue", "pr")
    ):
        intents.append("github_insight")
    if any(
        keyword in text
        for keyword in ("模板", "技术路线", "方案", "简历", "system design", "设计方案")
    ):
        intents.append("knowledge_template")
    if not intents:
        intents.append("general_chat")
    return intents


def _build_plan(intent: list[str]) -> list[str]:
    plan: list[str] = ["读取当前会话上下文，确认任务边界。", "召回长期记忆，避免输出风格和用户偏好丢失。"]
    if "document_analysis" in intent:
        plan.append("检查并读取相关上传文档，提取摘要或原文片段。")
    if "github_insight" in intent:
        plan.append("分析 GitHub 仓库公开信息，补充工程背景和活跃度。")
    if "knowledge_template" in intent:
        plan.append("读取知识模板，把结果整理成更适合方案/简历/技术路线的结构。")
    plan.append("综合工具观察，给出最终回答和下一步建议。")
    return plan


def _select_tool_names(intent: list[str]) -> list[str]:
    selected: list[str] = []
    if "document_analysis" in intent:
        selected.extend(["list_uploaded_documents", "read_uploaded_document"])
    if "github_insight" in intent:
        selected.append("inspect_github_repo")
    if "knowledge_template" in intent:
        selected.extend(["list_knowledge_templates", "read_knowledge_template"])
    return selected


def build_system_prompt(
    *,
    memory_context: str,
    intent: list[str],
    plan: list[str],
    selected_tools: list[str],
    prompt_version: str,
) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        prompt_version=prompt_version,
        intent_text="、".join(intent) if intent else "general_chat",
        plan_text="\n".join(f"- {item}" for item in plan) if plan else "- 直接回答用户问题。",
        tool_text="\n".join(f"- {item}" for item in selected_tools) if selected_tools else "- 当前轮不需要工具。",
        memory_text=memory_context or "暂无长期记忆。",
    )


def fallback_answer(
    prompt: str,
    *,
    memory_context: str,
    plan: list[str],
    selected_tools: list[str],
) -> str:
    parts = [
        "当前没有可用的 GLM 配置，已使用本地回退模式整理任务。",
        f"任务：{prompt}",
        "建议执行：",
        "\n".join(f"- {item}" for item in plan) if plan else "- 直接回答用户问题。",
    ]
    if selected_tools:
        parts.extend(
            [
                "按当前意图，下一步建议优先使用这些工具：",
                "\n".join(f"- {name}" for name in selected_tools),
            ]
        )
    if memory_context:
        parts.extend(["长期记忆：", memory_context])
    return "\n".join(parts)


def to_langchain_message(message: dict[str, Any]) -> HumanMessage | AIMessage:
    if message["role"] == "assistant":
        return AIMessage(content=message["content"])
    return HumanMessage(content=message["content"])


def _trim_observation(text: str, limit: int = 2200) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _parse_repo_input(repo: str) -> str:
    repo = repo.strip()
    match = re.search(r"github\.com/([^/\s]+/[^/\s#?]+)", repo)
    if match:
        return match.group(1)
    return repo.strip("/")


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _fallback_structured_output(
    prompt: str,
    *,
    draft_answer: str,
    plan: list[str],
    tool_log: list[dict[str, Any]],
) -> dict[str, Any]:
    highlights = [
        item.get("name", "")
        for item in tool_log
        if item.get("status") == "completed" and item.get("name")
    ][:3]
    return {
        "summaryTitle": prompt[:24] or "任务结果",
        "answer": draft_answer or "已完成任务整理，但没有拿到更完整的结构化输出。",
        "highlights": highlights,
        "nextSteps": plan[-2:] if len(plan) >= 2 else plan,
        "memoryWriteback": [],
    }


def _compose_final_answer(structured_output: dict[str, Any]) -> str:
    answer = str(structured_output.get("answer") or "").strip()
    highlights = [str(item).strip() for item in structured_output.get("highlights", []) if str(item).strip()]
    next_steps = [str(item).strip() for item in structured_output.get("nextSteps", []) if str(item).strip()]

    parts: list[str] = [answer or "任务已完成。"]
    if highlights:
        parts.extend(["", "关键点：", "\n".join(f"- {item}" for item in highlights)])
    if next_steps:
        parts.extend(["", "下一步建议：", "\n".join(f"- {item}" for item in next_steps)])
    return "\n".join(parts).strip()


async def run_agent_task(
    *,
    run_id: str,
    user_id: str,
    conversation_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    await upsert_prompt_version(
        PROMPT_KEY,
        PROMPT_VERSION,
        SYSTEM_PROMPT_TEMPLATE,
        metadata={"maxToolRounds": MAX_TOOL_ROUNDS},
    )
    prompt_version = await get_prompt_version(PROMPT_KEY, PROMPT_VERSION)
    prompt_version_label = prompt_version["version"] if prompt_version else PROMPT_VERSION

    await create_run_event(
        run_id,
        user_id,
        conversation_id,
        "run.started",
        {"promptVersion": prompt_version_label},
    )

    model = get_model()
    tool_log: list[dict[str, Any]] = []

    async def traced_node(name: str, state: AgentState, fn: Any) -> dict[str, Any]:
        step = await create_agent_step(
            run_id,
            user_id,
            conversation_id,
            name,
            input_data={
                "prompt": prompt,
                "toolRounds": state["tool_rounds"],
                "selectedTools": state["selected_tools"],
            },
        )
        await create_run_event(
            run_id,
            user_id,
            conversation_id,
            "step.started",
            {"stepId": step["id"], "name": name},
        )

        try:
            updates = await fn(state)
        except Exception as exc:
            await update_agent_step(step["id"], {"status": "failed", "error": str(exc)})
            await create_run_event(
                run_id,
                user_id,
                conversation_id,
                "step.failed",
                {"stepId": step["id"], "name": name, "error": str(exc)},
            )
            raise

        await update_agent_step(
            step["id"],
            {
                "status": "completed",
                "output": _serialize_for_store(updates),
            },
        )
        await create_run_event(
            run_id,
            user_id,
            conversation_id,
            "step.completed",
            {"stepId": step["id"], "name": name},
        )
        return updates

    async def invoke_tool(tool_name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await call_mcp_tool(tool_name, arguments)
            result_json = mcp_result_to_json(result)
            result_text = extract_mcp_text(result)
            entry = {
                "name": tool_name,
                "arguments": arguments,
                "result": result_text,
                "status": "completed",
            }
            tool_log.append(entry)
            await create_tool_call_log(
                run_id,
                conversation_id,
                user_id,
                tool_name,
                arguments,
                result=result_json,
                status="completed",
            )
            await create_run_event(
                run_id,
                user_id,
                conversation_id,
                "tool.completed",
                {"name": tool_name},
            )
            return result_text or json.dumps(result_json, ensure_ascii=False)
        except Exception as exc:
            entry = {
                "name": tool_name,
                "arguments": arguments,
                "error": str(exc),
                "status": "failed",
            }
            tool_log.append(entry)
            await create_tool_call_log(
                run_id,
                conversation_id,
                user_id,
                tool_name,
                arguments,
                result=None,
                status="failed",
                error=str(exc),
            )
            await create_run_event(
                run_id,
                user_id,
                conversation_id,
                "tool.failed",
                {"name": tool_name, "error": str(exc)},
            )
            raise

    @tool
    async def list_uploaded_documents(question: str = "") -> str:
        """列出当前会话已上传的文档，用于决定要读取哪份文档。"""

        return await invoke_tool(
            "list_uploaded_documents",
            {"userId": user_id, "conversationId": conversation_id, "question": question},
        )

    @tool
    async def read_uploaded_document(documentId: str, question: str = "") -> str:
        """读取指定上传文档的文本和摘要内容。"""

        return await invoke_tool(
            "read_uploaded_document",
            {"documentId": documentId, "question": question},
        )

    @tool
    async def inspect_github_repo(repo: str, question: str = "") -> str:
        """读取 GitHub 仓库信息、近期活跃度和公开工程指标。"""

        return await invoke_tool(
            "inspect_github_repo",
            {"repo": _parse_repo_input(repo), "question": question},
        )

    @tool
    async def list_knowledge_templates(question: str = "") -> str:
        """列出可用于方案、简历和技术路线整理的知识模板。"""

        return await invoke_tool("list_knowledge_templates", {"question": question})

    @tool
    async def read_knowledge_template(templateId: str, question: str = "") -> str:
        """读取指定知识模板的内容。"""

        return await invoke_tool(
            "read_knowledge_template",
            {"templateId": templateId, "question": question},
        )

    tool_map = {
        "list_uploaded_documents": list_uploaded_documents,
        "read_uploaded_document": read_uploaded_document,
        "inspect_github_repo": inspect_github_repo,
        "list_knowledge_templates": list_knowledge_templates,
        "read_knowledge_template": read_knowledge_template,
    }
    tool_node = ToolNode(list(tool_map.values()))

    async def load_context(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "load_context",
            state,
            lambda current_state: _load_context(current_state),
        )

    async def _load_context(state: AgentState) -> dict[str, Any]:
        history_length = len(state["messages"])
        context_summary = f"当前会话共有 {history_length} 条消息。"
        return {"context_summary": context_summary}

    async def retrieve_memory(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "retrieve_memory",
            state,
            lambda current_state: _retrieve_memory(current_state),
        )

    async def _retrieve_memory(_state: AgentState) -> dict[str, Any]:
        memory = await retrieve_memory_context(user_id, prompt)
        return {
            "memory_context": memory["memory_text"],
            "memory_source": memory["source"],
        }

    async def classify_intent(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "classify_intent",
            state,
            lambda current_state: _classify_intent(current_state),
        )

    async def _classify_intent(_state: AgentState) -> dict[str, Any]:
        return {"intent": _heuristic_intents(prompt, history)}

    async def plan_actions(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "plan_actions",
            state,
            lambda current_state: _plan_actions(current_state),
        )

    async def _plan_actions(state: AgentState) -> dict[str, Any]:
        return {"plan": _build_plan(state["intent"])}

    async def select_tools(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "select_tools",
            state,
            lambda current_state: _select_tools(current_state),
        )

    async def _select_tools(state: AgentState) -> dict[str, Any]:
        selected_tools = _select_tool_names(state["intent"])
        return {"selected_tools": selected_tools}

    async def call_model_step(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "call_model",
            state,
            lambda current_state: _call_model(current_state),
        )

    async def _call_model(state: AgentState) -> dict[str, Any]:
        if model is None:
            return {
                "messages": [
                    AIMessage(
                        content=fallback_answer(
                            prompt,
                            memory_context=state["memory_context"],
                            plan=state["plan"],
                            selected_tools=state["selected_tools"],
                        )
                    )
                ]
            }

        active_tools = [tool_map[name] for name in state["selected_tools"] if name in tool_map]
        current_model = model.bind_tools(active_tools) if active_tools else model
        response = await current_model.ainvoke(
            [SystemMessage(content=build_system_prompt(
                memory_context=state["memory_context"],
                intent=state["intent"],
                plan=state["plan"],
                selected_tools=state["selected_tools"],
                prompt_version=state["prompt_version"],
            ))]
            + state["messages"]
        )

        if isinstance(response, AIMessage):
            usage = _extract_usage(response)
            if usage["totalTokens"] > 0:
                await create_model_usage_log(
                    run_id,
                    user_id,
                    conversation_id,
                    model=usage["model"],
                    provider=usage["provider"],
                    stage="agent",
                    prompt_tokens=usage["promptTokens"],
                    completion_tokens=usage["completionTokens"],
                    total_tokens=usage["totalTokens"],
                    estimated_cost=usage["estimatedCost"],
                )
            return {"messages": [response], "model_usage": _merge_usage(state["model_usage"], usage)}

        return {"messages": [response]}

    async def run_tools(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "execute_tool_calls",
            state,
            lambda current_state: _run_tools(current_state),
        )

    async def _run_tools(state: AgentState) -> dict[str, Any]:
        result = await tool_node.ainvoke({"messages": state["messages"]})
        return {
            "messages": result["messages"],
            "tool_rounds": state["tool_rounds"] + 1,
        }

    async def reflect_on_observation(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "reflect_on_observation",
            state,
            lambda current_state: _reflect_on_observation(current_state),
        )

    async def _reflect_on_observation(state: AgentState) -> dict[str, Any]:
        tool_messages = [
            message for message in state["messages"][-4:] if isinstance(message, ToolMessage)
        ]
        latest_observation = "\n\n".join(
            _content_to_text(message.content) for message in tool_messages
        )
        return {"latest_observation": _trim_observation(latest_observation)}

    async def synthesize_output(state: AgentState) -> dict[str, Any]:
        return await traced_node(
            "synthesize_output",
            state,
            lambda current_state: _synthesize_output(current_state),
        )

    async def _synthesize_output(state: AgentState) -> dict[str, Any]:
        ai_messages = [item for item in state["messages"] if isinstance(item, AIMessage)]
        draft_answer = _content_to_text(ai_messages[-1].content) if ai_messages else ""

        if model is None:
            structured_output = _fallback_structured_output(
                prompt,
                draft_answer=draft_answer,
                plan=state["plan"],
                tool_log=tool_log,
            )
            return {
                "structured_output": structured_output,
                "final_answer": _compose_final_answer(structured_output),
            }

        recent_messages = [_serialize_message(item) for item in state["messages"][-10:]]
        response = await model.ainvoke(
            [
                (
                    "system",
                    "你是运行结果整理器。请只输出 JSON 对象，字段必须包含："
                    'summaryTitle, answer, highlights, nextSteps, memoryWriteback。'
                    "其中 highlights / nextSteps / memoryWriteback 必须是数组。"
                    "answer 是给用户看的最终中文 Markdown 回答。",
                ),
                (
                    "human",
                    json.dumps(
                        {
                            "prompt": prompt,
                            "memoryContext": state["memory_context"],
                            "plan": state["plan"],
                            "selectedTools": state["selected_tools"],
                            "latestObservation": state["latest_observation"],
                            "recentMessages": recent_messages,
                            "toolLog": tool_log[-6:],
                            "draftAnswer": draft_answer,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )

        if isinstance(response, AIMessage):
            usage = _extract_usage(response)
            if usage["totalTokens"] > 0:
                await create_model_usage_log(
                    run_id,
                    user_id,
                    conversation_id,
                    model=usage["model"],
                    provider=usage["provider"],
                    stage="synthesis",
                    prompt_tokens=usage["promptTokens"],
                    completion_tokens=usage["completionTokens"],
                    total_tokens=usage["totalTokens"],
                    estimated_cost=usage["estimatedCost"],
                )
        structured_output = _extract_json_object(_content_to_text(response.content))
        if structured_output is None:
            structured_output = _fallback_structured_output(
                prompt,
                draft_answer=draft_answer,
                plan=state["plan"],
                tool_log=tool_log,
            )

        structured_output.setdefault("summaryTitle", prompt[:24] or "任务结果")
        structured_output.setdefault("answer", draft_answer or "任务已完成。")
        structured_output.setdefault("highlights", [])
        structured_output.setdefault("nextSteps", [])
        structured_output.setdefault("memoryWriteback", [])

        model_usage = state["model_usage"]
        if isinstance(response, AIMessage):
            model_usage = _merge_usage(model_usage, _extract_usage(response))

        return {
            "structured_output": structured_output,
            "final_answer": _compose_final_answer(structured_output),
            "model_usage": model_usage,
        }

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS or not tool_calls:
            return "synthesize_output"
        return "tools"

    graph = (
        StateGraph(AgentState)
        .add_node("load_context", load_context)
        .add_node("retrieve_memory", retrieve_memory)
        .add_node("classify_intent", classify_intent)
        .add_node("plan_actions", plan_actions)
        .add_node("select_tools", select_tools)
        .add_node("agent", call_model_step)
        .add_node("tools", run_tools)
        .add_node("reflect_on_observation", reflect_on_observation)
        .add_node("synthesize_output", synthesize_output)
        .add_edge(START, "load_context")
        .add_edge("load_context", "retrieve_memory")
        .add_edge("retrieve_memory", "classify_intent")
        .add_edge("classify_intent", "plan_actions")
        .add_edge("plan_actions", "select_tools")
        .add_edge("select_tools", "agent")
        .add_conditional_edges("agent", should_continue, ["tools", "synthesize_output"])
        .add_edge("tools", "reflect_on_observation")
        .add_edge("reflect_on_observation", "agent")
        .add_edge("synthesize_output", END)
        .compile()
    )

    try:
        result = await graph.ainvoke(
            {
                "run_id": run_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "prompt": prompt,
                "messages": [to_langchain_message(item) for item in history],
                "memory_context": "",
                "memory_source": "",
                "context_summary": "",
                "intent": [],
                "plan": [],
                "selected_tools": [],
                "tool_rounds": 0,
                "latest_observation": "",
                "structured_output": None,
                "final_answer": "",
                "prompt_version": prompt_version_label,
                "model_usage": {
                    "promptTokens": 0,
                    "completionTokens": 0,
                    "totalTokens": 0,
                    "estimatedCost": 0.0,
                    "model": settings.glm_model,
                    "provider": "glm-compatible",
                },
            },
            {"recursion_limit": 20},
        )
    except Exception as exc:
        await create_run_event(
            run_id,
            user_id,
            conversation_id,
            "run.failed",
            {"error": str(exc)},
        )
        raise

    await create_run_event(
        run_id,
        user_id,
        conversation_id,
        "run.completed",
        {"toolRounds": result["tool_rounds"]},
    )

    return {
        "answer": result["final_answer"],
        "toolCalls": tool_log,
        "memoryContext": result["memory_context"],
        "memorySource": result["memory_source"],
        "structuredOutput": result["structured_output"],
        "promptVersion": result["prompt_version"],
        "modelUsage": result["model_usage"],
    }
