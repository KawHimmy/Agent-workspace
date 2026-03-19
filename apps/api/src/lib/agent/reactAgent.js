import { AIMessage, HumanMessage, SystemMessage } from "@langchain/core/messages";
import { tool } from "@langchain/core/tools";
import {
  Annotation,
  END,
  START,
  StateGraph,
  messagesStateReducer,
} from "@langchain/langgraph";
import { z } from "zod";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { callMcpTool } from "../mcp/client.js";
import { retrieveMemoryContext } from "../memory/mem0.js";
import { getModel } from "../llm.js";

const MAX_TOOL_ROUNDS = 2;

const AgentState = Annotation.Root({
  messages: Annotation({
    reducer: messagesStateReducer,
    default: () => [],
  }),
  memoryContext: Annotation({
    reducer: (_current, update) => update ?? _current,
    default: () => "",
  }),
  toolRounds: Annotation({
    reducer: (_current, update) => update ?? _current,
    default: () => 0,
  }),
});

function stringifyContent(content) {
  if (typeof content === "string") {
    return content;
  }

  return JSON.stringify(content);
}

function createDocumentTools({ userId, conversationId, toolLog }) {
  const listDocuments = tool(
    async ({ question }) => {
      const result = await callMcpTool("list_uploaded_documents", {
        userId,
        conversationId,
      });

      toolLog.push({
        name: "list_uploaded_documents",
        question,
        result: result.content?.map((item) => item.text).join("\n") ?? "",
      });

      return result.content?.map((item) => item.text).join("\n") ?? "暂无文档。";
    },
    {
      name: "list_uploaded_documents",
      description: "当用户提到上传的文档、附件、需求文档或方案时，先用这个工具查看有哪些文档可用。",
      schema: z.object({
        question: z.string().describe("当前为什么要查看文档列表。"),
      }),
    },
  );

  const readDocument = tool(
    async ({ documentId, question }) => {
      const result = await callMcpTool("read_uploaded_document", {
        documentId,
      });

      toolLog.push({
        name: "read_uploaded_document",
        question,
        documentId,
        result: result.content?.map((item) => item.text).join("\n") ?? "",
      });

      return result.content?.map((item) => item.text).join("\n") ?? "未读取到文档内容。";
    },
    {
      name: "read_uploaded_document",
      description: "读取某个上传文档的摘要与正文摘录。",
      schema: z.object({
        documentId: z.string().describe("需要读取的文档 ID。"),
        question: z.string().describe("当前想从文档中获取什么信息。"),
      }),
    },
  );

  return [listDocuments, readDocument];
}

function shouldContinue(state) {
  const lastMessage = state.messages[state.messages.length - 1];

  if (
    state.toolRounds >= MAX_TOOL_ROUNDS ||
    !("tool_calls" in lastMessage) ||
    !Array.isArray(lastMessage.tool_calls) ||
    lastMessage.tool_calls.length === 0
  ) {
    return END;
  }

  return "tools";
}

function buildSystemPrompt(memoryContext) {
  return [
    "你是 ReAct Agent Workspace 的最小可用智能体。",
    "你的目标是帮助用户分析任务、在必要时调用上传文档工具，并输出结构化、可执行的结果。",
    "如果用户明确提到上传的文档、附件、方案、需求、PDF 或 markdown，请优先调用工具查看文档。",
    "请控制工具调用次数，只有真正需要时才调用工具。",
    memoryContext ? `以下是该用户的长期记忆/偏好：\n${memoryContext}` : "当前没有可用的长期记忆。",
  ].join("\n\n");
}

function fallbackAnswer(prompt, memoryContext) {
  return [
    "当前没有检测到可用的 GLM 模型配置，所以返回本地降级结果。",
    `任务内容：${prompt}`,
    memoryContext ? `长期记忆：${memoryContext}` : "长期记忆：暂无",
    "你现在可以先上传文档或补充任务描述；一旦补上模型配置，就会自动走 LangGraph + MCP 的完整流程。",
  ].join("\n");
}

function toLangChainMessage(message) {
  if (message.role === "assistant") {
    return new AIMessage({ content: message.content });
  }

  return new HumanMessage(message.content);
}

export async function runAgentTask({
  userId,
  conversationId,
  prompt,
  history,
}) {
  const memory = await retrieveMemoryContext(userId, prompt);
  const model = getModel();
  const toolLog = [];

  if (!model) {
    return {
      answer: fallbackAnswer(prompt, memory.memoryText),
      toolCalls: [],
      memoryContext: memory.memoryText,
      memorySource: memory.source,
    };
  }

  const tools = createDocumentTools({
    userId,
    conversationId,
    toolLog,
  });

  const toolNode = new ToolNode(tools);
  const modelWithTools = model.bindTools(tools);

  const callModel = async (state) => {
    const response = await modelWithTools.invoke([
      new SystemMessage(buildSystemPrompt(state.memoryContext)),
      ...state.messages,
    ]);

    return {
      messages: [response],
    };
  };

  const runTools = async (state) => {
    const result = await toolNode.invoke({
      messages: state.messages,
    });

    return {
      messages: result.messages,
      toolRounds: state.toolRounds + 1,
    };
  };

  const graph = new StateGraph(AgentState)
    .addNode("agent", callModel)
    .addNode("tools", runTools)
    .addEdge(START, "agent")
    .addConditionalEdges("agent", shouldContinue, ["tools", END])
    .addEdge("tools", "agent")
    .compile();

  const result = await graph.invoke(
    {
      messages: history.map(toLangChainMessage),
      memoryContext: memory.memoryText,
      toolRounds: 0,
    },
    {
      recursionLimit: 8,
    },
  );

  const answerMessage = result.messages
    .filter((message) => message instanceof AIMessage)
    .at(-1);

  return {
    answer: answerMessage ? stringifyContent(answerMessage.content) : "未生成有效回复。",
    toolCalls: toolLog,
    memoryContext: memory.memoryText,
    memorySource: memory.source,
  };
}
