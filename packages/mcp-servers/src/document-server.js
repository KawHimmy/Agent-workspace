import fs from "node:fs/promises";
import path from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const storeFile = process.env.APP_STORE_FILE;
const githubToken = process.env.GITHUB_TOKEN;

const knowledgeTemplates = {
  "resume-project": {
    id: "resume-project",
    title: "简历项目模板",
    category: "resume",
    content: [
      "项目名称：",
      "一句话定位：",
      "技术栈：",
      "你负责的核心模块：",
      "工程亮点：",
      "- 架构设计",
      "- 可观测性 / trace / logging",
      "- 异步任务与重试",
      "- 长期记忆 / 检索 / MCP 工具",
      "量化结果：",
      "面试时可展开的权衡：",
    ].join("\n"),
  },
  "system-design": {
    id: "system-design",
    title: "系统设计输出模板",
    category: "architecture",
    content: [
      "1. 目标与边界",
      "2. 核心流程",
      "3. 模块拆分",
      "4. 数据模型",
      "5. 关键技术选型与原因",
      "6. 失败场景与恢复策略",
      "7. 可观测性与调试",
      "8. 后续演进路线",
    ].join("\n"),
  },
  "technical-route-checklist": {
    id: "technical-route-checklist",
    title: "技术路线核对清单",
    category: "planning",
    content: [
      "- 核心 runtime 是否成型",
      "- 是否有多节点工作流",
      "- 是否有工具协议层与工具注册",
      "- 是否有长期记忆与写回策略",
      "- 是否有异步任务、状态和重试",
      "- 是否有 trace / tool log / audit log",
      "- 是否有 token/cost tracking",
      "- 是否有前端调试面板或 replay 入口",
    ].join("\n"),
  },
};

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
  return String(text ?? "").replace(/\s+/g, " ").trim();
}

function githubHeaders() {
  return {
    Accept: "application/vnd.github+json",
    "User-Agent": "react-agent-workspace",
    ...(githubToken ? { Authorization: `Bearer ${githubToken}` } : {}),
  };
}

function parseRepo(repo) {
  const normalized = String(repo ?? "").trim();
  const fromUrl = normalized.match(/github\.com\/([^/\s]+\/[^/\s#?]+)/i);
  const repoPath = (fromUrl?.[1] ?? normalized).replace(/^\/+|\/+$/g, "");
  const [owner, name] = repoPath.split("/");

  if (!owner || !name) {
    throw new Error("repo 必须是 owner/name 或 GitHub 仓库 URL");
  }

  return `${owner}/${name.replace(/\.git$/i, "")}`;
}

async function fetchGithubJson(url) {
  const response = await fetch(url, { headers: githubHeaders() });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`GitHub API 请求失败: ${response.status} ${detail}`);
  }
  return response.json();
}

async function inspectGithubRepo(repo) {
  const repoPath = parseRepo(repo);
  const baseUrl = `https://api.github.com/repos/${repoPath}`;
  const [repoInfo, commits, pulls, issues] = await Promise.all([
    fetchGithubJson(baseUrl),
    fetchGithubJson(`${baseUrl}/commits?per_page=5`),
    fetchGithubJson(`${baseUrl}/pulls?state=all&per_page=5`),
    fetchGithubJson(`${baseUrl}/issues?state=all&per_page=5`),
  ]);

  return {
    repo: repoPath,
    description: repoInfo.description,
    language: repoInfo.language,
    stars: repoInfo.stargazers_count,
    forks: repoInfo.forks_count,
    openIssues: repoInfo.open_issues_count,
    defaultBranch: repoInfo.default_branch,
    license: repoInfo.license?.spdx_id ?? null,
    pushedAt: repoInfo.pushed_at,
    homepage: repoInfo.homepage,
    topics: repoInfo.topics ?? [],
    recentCommits: Array.isArray(commits)
      ? commits.map((item) => ({
          sha: item.sha?.slice(0, 7),
          message: item.commit?.message?.split("\n")[0] ?? "",
          author: item.commit?.author?.name ?? "",
          date: item.commit?.author?.date ?? "",
        }))
      : [],
    recentPulls: Array.isArray(pulls)
      ? pulls.map((item) => ({
          number: item.number,
          title: item.title,
          state: item.state,
          mergedAt: item.merged_at,
          updatedAt: item.updated_at,
        }))
      : [],
    recentIssues: Array.isArray(issues)
      ? issues
          .filter((item) => !item.pull_request)
          .map((item) => ({
            number: item.number,
            title: item.title,
            state: item.state,
            updatedAt: item.updated_at,
          }))
      : [],
  };
}

const server = new McpServer({
  name: "workspace-mcp-server",
  version: "2.0.0",
});

server.registerTool(
  "list_uploaded_documents",
  {
    description: "列出当前会话内已经上传的文档，帮助 agent 判断是否需要继续读取文档内容。",
    inputSchema: {
      userId: z.string(),
      conversationId: z.string().optional(),
      question: z.string().optional(),
    },
  },
  async ({ userId, conversationId }) => {
    const store = await readStore();
    const documents = (store.documents ?? [])
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
      question: z.string().optional(),
    },
  },
  async ({ documentId }) => {
    const store = await readStore();
    const document = (store.documents ?? []).find((item) => item.id === documentId);

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

server.registerTool(
  "inspect_github_repo",
  {
    description: "查询 GitHub 仓库公开信息、近期提交、PR 和 issue，帮助总结工程亮点。",
    inputSchema: {
      repo: z.string(),
      question: z.string().optional(),
    },
  },
  async ({ repo }) => {
    const payload = await inspectGithubRepo(repo);
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(payload, null, 2),
        },
      ],
    };
  },
);

server.registerTool(
  "list_knowledge_templates",
  {
    description: "列出当前内置的知识模板，适合方案、简历和技术路线整理。",
    inputSchema: {
      question: z.string().optional(),
    },
  },
  async () => {
    const payload = Object.values(knowledgeTemplates).map((item) => ({
      id: item.id,
      title: item.title,
      category: item.category,
    }));

    return {
      content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    };
  },
);

server.registerTool(
  "read_knowledge_template",
  {
    description: "读取指定知识模板的正文内容。",
    inputSchema: {
      templateId: z.string(),
      question: z.string().optional(),
    },
  },
  async ({ templateId }) => {
    const template = knowledgeTemplates[templateId];
    if (!template) {
      return {
        content: [{ type: "text", text: "未找到对应模板。" }],
      };
    }

    return {
      content: [{ type: "text", text: JSON.stringify(template, null, 2) }],
    };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
