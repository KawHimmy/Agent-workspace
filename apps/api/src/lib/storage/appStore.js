import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { env } from "../../config/env.js";

const INITIAL_STORE = {
  conversations: [],
  messages: [],
  agentRuns: [],
  documents: [],
  backgroundJobs: [],
  userPreferences: [],
};

async function ensureStore() {
  await fs.mkdir(path.dirname(env.storeFile), { recursive: true });

  try {
    await fs.access(env.storeFile);
  } catch {
    await fs.writeFile(env.storeFile, JSON.stringify(INITIAL_STORE, null, 2), "utf8");
  }
}

async function readStore() {
  await ensureStore();
  const raw = await fs.readFile(env.storeFile, "utf8");

  // 兼容被 Windows/PowerShell 以 UTF-8 BOM 写入的 JSON 文件，避免开头的不可见字符导致解析失败。
  const normalized = raw.replace(/^\uFEFF/, "").trim();

  try {
    return JSON.parse(normalized || JSON.stringify(INITIAL_STORE));
  } catch {
    // 如果本地 store 被手动改坏了，自动回退到一个可用的空结构，保证登录和页面初始化不被阻塞。
    await writeStore(INITIAL_STORE);
    return structuredClone(INITIAL_STORE);
  }
}

async function writeStore(nextStore) {
  await fs.writeFile(env.storeFile, JSON.stringify(nextStore, null, 2), "utf8");
  return nextStore;
}

function sortByUpdatedAtDesc(items) {
  return [...items].sort((left, right) => {
    return new Date(right.updatedAt ?? right.createdAt).getTime() - new Date(left.updatedAt ?? left.createdAt).getTime();
  });
}

export async function listConversationsByUser(userId) {
  const store = await readStore();
  return sortByUpdatedAtDesc(store.conversations.filter((item) => item.userId === userId));
}

export async function createConversation(userId, title = "新的任务") {
  const store = await readStore();
  const now = new Date().toISOString();
  const conversation = {
    id: randomUUID(),
    userId,
    title,
    createdAt: now,
    updatedAt: now,
  };

  store.conversations.push(conversation);
  await writeStore(store);
  return conversation;
}

export async function getConversationById(conversationId, userId) {
  const store = await readStore();
  const conversation = store.conversations.find((item) => item.id === conversationId && item.userId === userId);

  if (!conversation) {
    return null;
  }

  return {
    ...conversation,
    messages: store.messages.filter((item) => item.conversationId === conversationId),
    documents: sortByUpdatedAtDesc(
      store.documents.filter((item) => item.conversationId === conversationId && item.userId === userId),
    ),
  };
}

export async function appendMessage({
  conversationId,
  userId,
  role,
  content,
  metadata = {},
}) {
  const store = await readStore();
  const now = new Date().toISOString();
  const message = {
    id: randomUUID(),
    conversationId,
    userId,
    role,
    content,
    metadata,
    createdAt: now,
    updatedAt: now,
  };

  store.messages.push(message);

  const conversation = store.conversations.find((item) => item.id === conversationId);
  if (conversation) {
    conversation.updatedAt = now;
    if (role === "user" && content?.trim()) {
      conversation.title = conversation.title === "新的任务" ? content.slice(0, 30) : conversation.title;
    }
  }

  await writeStore(store);
  return message;
}

export async function createAgentRun({
  conversationId,
  userId,
  prompt,
}) {
  const store = await readStore();
  const now = new Date().toISOString();
  const run = {
    id: randomUUID(),
    conversationId,
    userId,
    prompt,
    status: "running",
    toolCalls: [],
    memoryContext: "",
    result: "",
    createdAt: now,
    updatedAt: now,
  };

  store.agentRuns.push(run);
  await writeStore(store);
  return run;
}

export async function updateAgentRun(runId, updates) {
  const store = await readStore();
  const run = store.agentRuns.find((item) => item.id === runId);

  if (!run) {
    return null;
  }

  Object.assign(run, updates, { updatedAt: new Date().toISOString() });
  await writeStore(store);
  return run;
}

export async function listAgentRunsByUser(userId) {
  const store = await readStore();
  return sortByUpdatedAtDesc(store.agentRuns.filter((item) => item.userId === userId));
}

export async function createDocument(record) {
  const store = await readStore();
  const now = new Date().toISOString();
  const document = {
    id: randomUUID(),
    status: "queued",
    summary: "",
    extractedText: "",
    createdAt: now,
    updatedAt: now,
    ...record,
  };

  store.documents.push(document);
  await writeStore(store);
  return document;
}

export async function updateDocument(documentId, updates) {
  const store = await readStore();
  const document = store.documents.find((item) => item.id === documentId);

  if (!document) {
    return null;
  }

  Object.assign(document, updates, { updatedAt: new Date().toISOString() });
  await writeStore(store);
  return document;
}

export async function getDocumentById(documentId, userId) {
  const store = await readStore();
  return store.documents.find((item) => item.id === documentId && item.userId === userId) ?? null;
}

export async function listDocumentsByUser(userId) {
  const store = await readStore();
  return sortByUpdatedAtDesc(store.documents.filter((item) => item.userId === userId));
}

export async function createBackgroundJob(record) {
  const store = await readStore();
  const now = new Date().toISOString();
  const job = {
    id: randomUUID(),
    status: "queued",
    output: null,
    error: null,
    createdAt: now,
    updatedAt: now,
    ...record,
  };

  store.backgroundJobs.push(job);
  await writeStore(store);
  return job;
}

export async function updateBackgroundJob(jobId, updates) {
  const store = await readStore();
  const job = store.backgroundJobs.find((item) => item.id === jobId);

  if (!job) {
    return null;
  }

  Object.assign(job, updates, { updatedAt: new Date().toISOString() });
  await writeStore(store);
  return job;
}

export async function listBackgroundJobsByUser(userId) {
  const store = await readStore();
  return sortByUpdatedAtDesc(store.backgroundJobs.filter((item) => item.userId === userId));
}

export async function upsertPreference({ userId, key, value, source = "app" }) {
  const store = await readStore();
  const now = new Date().toISOString();
  const existing = store.userPreferences.find((item) => item.userId === userId && item.key === key);

  if (existing) {
    Object.assign(existing, { value, source, updatedAt: now });
  } else {
    store.userPreferences.push({
      id: randomUUID(),
      userId,
      key,
      value,
      source,
      createdAt: now,
      updatedAt: now,
    });
  }

  await writeStore(store);
}

export async function listPreferencesByUser(userId) {
  const store = await readStore();
  return sortByUpdatedAtDesc(store.userPreferences.filter((item) => item.userId === userId));
}

export async function getRawStore() {
  return readStore();
}
