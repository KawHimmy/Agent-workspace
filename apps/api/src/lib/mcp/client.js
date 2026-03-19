import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { CallToolResultSchema, ListToolsResultSchema } from "@modelcontextprotocol/sdk/types.js";
import { env } from "../../config/env.js";

let mcpClientPromise;

async function createClient() {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ["packages/mcp-servers/src/document-server.js"],
    cwd: env.rootDir,
    env: {
      ...process.env,
      APP_STORE_FILE: env.storeFile,
    },
    stderr: "pipe",
  });

  if (transport.stderr) {
    transport.stderr.on("data", (chunk) => {
      // 使用 stderr 打印子进程日志，避免污染 MCP 标准输出流。
      process.stderr.write(chunk);
    });
  }

  const client = new Client({
    name: "react-agent-workspace-client",
    version: "1.0.0",
  });

  await client.connect(transport);
  return client;
}

export async function getMcpClient() {
  if (!mcpClientPromise) {
    mcpClientPromise = createClient();
  }

  return mcpClientPromise;
}

export async function listMcpTools() {
  const client = await getMcpClient();
  return client.request({ method: "tools/list", params: {} }, ListToolsResultSchema);
}

export async function callMcpTool(name, args) {
  const client = await getMcpClient();
  return client.request(
    {
      method: "tools/call",
      params: {
        name,
        arguments: args,
      },
    },
    CallToolResultSchema,
  );
}
