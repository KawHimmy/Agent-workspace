import fs from "node:fs/promises";
import path from "node:path";
import cors from "cors";
import express from "express";
import multer from "multer";
import { toNodeHandler } from "better-auth/node";
import { tasks } from "@trigger.dev/sdk/v3";
import { env } from "./config/env.js";
import { auth } from "./lib/auth/auth.js";
import { requireSession, getSessionFromRequest } from "./lib/auth/session.js";
import { runAgentTask } from "./lib/agent/reactAgent.js";
import { listMcpTools } from "./lib/mcp/client.js";
import { listPreferencesByUser, listConversationsByUser, createConversation, getConversationById, appendMessage, createAgentRun, updateAgentRun, createDocument, getDocumentById, listDocumentsByUser, createBackgroundJob, listBackgroundJobsByUser, updateBackgroundJob, updateDocument } from "./lib/storage/appStore.js";
import { processDocumentSummary } from "./lib/documents/summary.js";
import { writeConversationMemory } from "./lib/memory/mem0.js";

const app = express();
const webRoot = path.join(env.rootDir, "apps", "web", "static");

const upload = multer({
  dest: env.uploadsDir,
  limits: {
    fileSize: 10 * 1024 * 1024,
  },
});

await fs.mkdir(env.uploadsDir, { recursive: true });

app.use(
  cors({
    origin: env.appUrl,
    credentials: true,
  }),
);

app.use(express.json({ limit: "1mb" }));
app.use(express.urlencoded({ extended: true }));

function copySetCookieHeaders(response, res) {
  if (typeof response.headers.getSetCookie === "function") {
    for (const cookie of response.headers.getSetCookie()) {
      res.append("set-cookie", cookie);
    }
  }
}

async function sendWebResponse(response, res) {
  copySetCookieHeaders(response, res);
  const text = await response.text();
  res.status(response.status);
  if (response.headers.get("content-type")) {
    res.setHeader("content-type", response.headers.get("content-type"));
  }
  res.send(text);
}

async function proxyBetterAuth(req, res, targetPath) {
  const response = await fetch(new URL(targetPath, env.appUrl), {
    method: req.method,
    headers: {
      "content-type": "application/json",
      origin: env.appUrl,
      cookie: req.headers.cookie ?? "",
    },
    body: JSON.stringify(req.body ?? {}),
  });

  await sendWebResponse(response, res);
}

async function scheduleDocumentSummary(payload) {
  try {
    await tasks.trigger("document-summary", payload);
    return "trigger.dev";
  } catch {
    setTimeout(async () => {
      try {
        await processDocumentSummary(payload);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown error";
        await updateDocument(payload.documentId, {
          status: "failed",
          summary: message,
        });
        await updateBackgroundJob(payload.jobId, {
          status: "failed",
          error: message,
        });
      }
    }, 50);

    return "local-fallback";
  }
}

app.post("/api/auth/register", async (req, res) => {
  await proxyBetterAuth(req, res, "/api/auth/sign-up/email");
});

app.post("/api/auth/login", async (req, res) => {
  await proxyBetterAuth(req, res, "/api/auth/sign-in/email");
});

app.post("/api/auth/logout", async (req, res) => {
  await proxyBetterAuth(req, res, "/api/auth/sign-out");
});

// Better Auth 放在包装接口之后，这样 /api/auth/login 等自定义路径不会被通配路由吞掉。
app.all("/api/auth/{*authPath}", toNodeHandler(auth));

app.get("/api/me", async (req, res) => {
  const session = await getSessionFromRequest(req);
  res.json(session?.user ?? null);
});

app.get("/api/bootstrap", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  const [conversations, documents, jobs, preferences] = await Promise.all([
    listConversationsByUser(session.user.id),
    listDocumentsByUser(session.user.id),
    listBackgroundJobsByUser(session.user.id),
    listPreferencesByUser(session.user.id),
  ]);

  res.json({
    user: session.user,
    conversations,
    documents,
    jobs,
    preferences,
  });
});

app.get("/api/tools", async (_req, res) => {
  try {
    const tools = await listMcpTools();
    res.json(tools);
  } catch (error) {
    res.status(500).json({
      error: error instanceof Error ? error.message : "加载工具失败",
    });
  }
});

app.get("/api/conversations", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  res.json(await listConversationsByUser(session.user.id));
});

app.post("/api/conversations", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  const conversation = await createConversation(
    session.user.id,
    req.body?.title?.trim() || "新的任务",
  );

  res.status(201).json(conversation);
});

app.get("/api/conversations/:id", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  const conversation = await getConversationById(req.params.id, session.user.id);
  if (!conversation) {
    res.status(404).json({ error: "未找到会话。" });
    return;
  }

  res.json(conversation);
});

app.post("/api/agent/run", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  const prompt = req.body?.prompt?.trim();
  if (!prompt) {
    res.status(400).json({ error: "prompt 不能为空。" });
    return;
  }

  const conversationId =
    req.body?.conversationId || (await createConversation(session.user.id, prompt.slice(0, 30))).id;

  await appendMessage({
    conversationId,
    userId: session.user.id,
    role: "user",
    content: prompt,
  });

  const run = await createAgentRun({
    conversationId,
    userId: session.user.id,
    prompt,
  });

  try {
    const conversation = await getConversationById(conversationId, session.user.id);
    const agentResult = await runAgentTask({
      userId: session.user.id,
      conversationId,
      prompt,
      history: conversation?.messages ?? [],
    });

    const assistantMessage = await appendMessage({
      conversationId,
      userId: session.user.id,
      role: "assistant",
      content: agentResult.answer,
      metadata: {
        toolCalls: agentResult.toolCalls,
        memorySource: agentResult.memorySource,
      },
    });

    await writeConversationMemory({
      userId: session.user.id,
      userMessage: prompt,
      assistantMessage: agentResult.answer,
    });

    const completedRun = await updateAgentRun(run.id, {
      status: "completed",
      result: agentResult.answer,
      toolCalls: agentResult.toolCalls,
      memoryContext: agentResult.memoryContext,
    });

    res.json({
      conversationId,
      message: assistantMessage,
      run: completedRun,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Agent 执行失败";
    await updateAgentRun(run.id, {
      status: "failed",
      result: message,
    });
    res.status(500).json({ error: message });
  }
});

app.post("/api/documents/upload", upload.single("document"), async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  if (!req.file) {
    res.status(400).json({ error: "请选择需要上传的文件。" });
    return;
  }

  const conversationId =
    req.body?.conversationId || (await createConversation(session.user.id, "文档分析")).id;

  const document = await createDocument({
    userId: session.user.id,
    conversationId,
    originalName: req.file.originalname,
    contentType: req.file.mimetype,
    size: req.file.size,
    filePath: req.file.path,
  });

  const job = await createBackgroundJob({
    userId: session.user.id,
    conversationId,
    documentId: document.id,
    type: "document-summary",
  });

  const queueProvider = await scheduleDocumentSummary({
    documentId: document.id,
    userId: session.user.id,
    jobId: job.id,
  });

  res.status(202).json({
    conversationId,
    document,
    job: {
      ...job,
      queueProvider,
    },
  });
});

app.get("/api/documents/:id", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  const document = await getDocumentById(req.params.id, session.user.id);
  if (!document) {
    res.status(404).json({ error: "未找到文档。" });
    return;
  }

  res.json(document);
});

app.get("/api/jobs", async (req, res) => {
  const session = await requireSession(req, res);
  if (!session) {
    return;
  }

  res.json(await listBackgroundJobsByUser(session.user.id));
});

app.use(express.static(webRoot));

app.get(/^(?!\/api).*/, (_req, res) => {
  res.sendFile(path.join(webRoot, "index.html"));
});

app.listen(env.port, () => {
  console.log(`ReAct Agent Workspace is running at ${env.appUrl}`);
});
