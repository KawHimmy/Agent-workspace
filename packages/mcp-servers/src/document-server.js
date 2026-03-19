import fs from "node:fs/promises";
import path from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const storeFile = process.env.APP_STORE_FILE;

async function readStore() {
  if (!storeFile) {
    return { documents: [] };
  }

  try {
    const raw = await fs.readFile(storeFile, "utf8");
    return JSON.parse(raw);
  } catch {
    return { documents: [] };
  }
}

function normalizeText(text) {
  return text.replace(/\s+/g, " ").trim();
}

const server = new McpServer({
  name: "document-mcp-server",
  version: "1.0.0",
});

server.registerTool(
  "list_uploaded_documents",
  {
    description: "列出当前会话内已经上传的文档，帮助 agent 判断是否需要继续读取文档内容。",
    inputSchema: {
      userId: z.string(),
      conversationId: z.string().optional(),
    },
  },
  async ({ userId, conversationId }) => {
    const store = await readStore();
    const documents = store.documents
      .filter((item) => item.userId === userId)
      .filter((item) => (conversationId ? item.conversationId === conversationId : true))
      .map((item) => ({
        id: item.id,
        name: item.originalName,
        status: item.status,
        summary: item.summary,
      }));

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(documents, null, 2),
        },
      ],
    };
  },
);

server.registerTool(
  "read_uploaded_document",
  {
    description: "读取指定上传文档的文本和摘要，便于 agent 做进一步分析。",
    inputSchema: {
      documentId: z.string(),
    },
  },
  async ({ documentId }) => {
    const store = await readStore();
    const document = store.documents.find((item) => item.id === documentId);

    if (!document) {
      return {
        content: [{ type: "text", text: "未找到对应文档。" }],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              id: document.id,
              name: document.originalName,
              status: document.status,
              summary: document.summary,
              extractedText: normalizeText(document.extractedText ?? "").slice(0, 5000),
              filePath: path.basename(document.filePath ?? ""),
            },
            null,
            2,
          ),
        },
      ],
    };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
