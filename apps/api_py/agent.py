from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .llm import get_model
from .mcp_client import call_mcp_tool
from .memory import retrieve_memory_context

MAX_TOOL_ROUNDS = 2


class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    memory_context: str
    tool_rounds: int


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def build_system_prompt(memory_context: str) -> str:
    return "\n\n".join(
        [
            "You are the minimal ReAct agent for ReAct Agent Workspace.",
            "Help the user with uploaded documents, memory-aware answers, and concise execution plans.",
            "If the user mentions uploaded documents, attachments, plans, markdown, or PDFs, use tools before answering.",
            "Use tools only when needed and keep the number of tool rounds small.",
            memory_context or "No long-term memory is currently available.",
        ]
    )


def fallback_answer(prompt: str, memory_context: str) -> str:
    parts = [
        "The Python backend is running, but no GLM configuration was found for the LangGraph agent.",
        f"Task: {prompt}",
    ]
    if memory_context:
        parts.append(f"Memory: {memory_context}")
    return "\n".join(parts)


def to_langchain_message(message: dict[str, Any]) -> HumanMessage | AIMessage:
    if message["role"] == "assistant":
        return AIMessage(content=message["content"])
    return HumanMessage(content=message["content"])


async def run_agent_task(
    user_id: str,
    conversation_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    memory = await retrieve_memory_context(user_id, prompt)
    model = get_model()
    tool_log: list[dict[str, Any]] = []

    if model is None:
        return {
            "answer": fallback_answer(prompt, memory["memory_text"]),
            "toolCalls": [],
            "memoryContext": memory["memory_text"],
            "memorySource": memory["source"],
        }

    @tool
    async def list_uploaded_documents(question: str) -> str:
        """List uploaded documents for the current conversation."""

        result = await call_mcp_tool(
            "list_uploaded_documents",
            {"userId": user_id, "conversationId": conversation_id},
        )
        text = "\n".join(
            item.text for item in result.content if getattr(item, "text", None)
        )
        tool_log.append(
            {
                "name": "list_uploaded_documents",
                "question": question,
                "result": text,
            }
        )
        return text or "No documents available."

    @tool
    async def read_uploaded_document(documentId: str, question: str) -> str:
        """Read a specific uploaded document."""

        result = await call_mcp_tool(
            "read_uploaded_document", {"documentId": documentId}
        )
        text = "\n".join(
            item.text for item in result.content if getattr(item, "text", None)
        )
        tool_log.append(
            {
                "name": "read_uploaded_document",
                "question": question,
                "documentId": documentId,
                "result": text,
            }
        )
        return text or "Document content was not available."

    tools = [list_uploaded_documents, read_uploaded_document]
    tool_node = ToolNode(tools)
    model_with_tools = model.bind_tools(tools)

    async def call_model(state: AgentState) -> dict[str, Any]:
        response = await model_with_tools.ainvoke(
            [SystemMessage(content=build_system_prompt(state["memory_context"]))]
            + state["messages"]
        )
        return {"messages": [response]}

    async def run_tools(state: AgentState) -> dict[str, Any]:
        result = await tool_node.ainvoke({"messages": state["messages"]})
        return {
            "messages": result["messages"],
            "tool_rounds": state["tool_rounds"] + 1,
        }

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS or not tool_calls:
            return END
        return "tools"

    graph = (
        StateGraph(AgentState)
        .add_node("agent", call_model)
        .add_node("tools", run_tools)
        .add_edge(START, "agent")
        .add_conditional_edges("agent", should_continue, ["tools", END])
        .add_edge("tools", "agent")
        .compile()
    )

    result = await graph.ainvoke(
        {
            "messages": [to_langchain_message(item) for item in history],
            "memory_context": memory["memory_text"],
            "tool_rounds": 0,
        },
        {"recursion_limit": 8},
    )

    ai_messages = [item for item in result["messages"] if isinstance(item, AIMessage)]
    answer = _content_to_text(ai_messages[-1].content) if ai_messages else "No answer generated."

    return {
        "answer": answer,
        "toolCalls": tool_log,
        "memoryContext": memory["memory_text"],
        "memorySource": memory["source"],
    }
