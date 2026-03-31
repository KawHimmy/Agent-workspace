const state = {
  user: null,
  conversations: [],
  activeConversationId: null,
  activeConversation: null,
  documents: [],
  jobs: [],
  preferences: [],
  memoryWritebacks: [],
  tools: [],
  toolLogs: [],
  runs: [],
  workspaces: [],
  currentWorkspace: null,
  activeRunId: null,
  activeRun: null,
  isSending: false,
  pendingPrompt: "",
  toolTestResult: null,
};

const authPanel = document.querySelector("#auth-panel");
const sessionPanel = document.querySelector("#session-panel");
const authHint = document.querySelector("#auth-hint");
const userName = document.querySelector("#user-name");
const userEmail = document.querySelector("#user-email");
const workspaceSelect = document.querySelector("#workspace-select");
const currentWorkspaceRole = document.querySelector("#current-workspace-role");
const statusBanner = document.querySelector("#status-banner");
const conversationList = document.querySelector("#conversation-list");
const messageList = document.querySelector("#message-list");
const documentList = document.querySelector("#document-list");
const jobList = document.querySelector("#job-list");
const preferenceList = document.querySelector("#preference-list");
const memoryWritebackList = document.querySelector("#memory-writeback-list");
const toolList = document.querySelector("#tool-list");
const runList = document.querySelector("#run-list");
const runDetail = document.querySelector("#run-detail");
const activeConversationLabel = document.querySelector("#active-conversation-label");
const activeRunLabel = document.querySelector("#active-run-label");
const promptInput = document.querySelector("#prompt-input");
const sendButton = document.querySelector("#send-button");
const conversationCount = document.querySelector("#conversation-count");
const documentCount = document.querySelector("#document-count");
const jobCount = document.querySelector("#job-count");
const preferenceCount = document.querySelector("#preference-count");
const toolCount = document.querySelector("#tool-count");
const runCount = document.querySelector("#run-count");
const toolSelect = document.querySelector("#tool-select");
const toolArguments = document.querySelector("#tool-arguments");
const toolTestResult = document.querySelector("#tool-test-result");

async function apiFetch(url, options = {}) {
  const workspaceHeaders =
    state.currentWorkspace?.id && !String(url).startsWith("/api/auth/")
      ? { "X-Workspace-Id": state.currentWorkspace.id }
      : {};

  const response = await fetch(url, {
    credentials: "include",
    headers: {
      ...workspaceHeaders,
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers ?? {}),
    },
    ...options,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw new Error(payload?.error || payload?.detail || payload?.message || payload || "请求失败");
  }

  return payload;
}

function setStatus(message, variant = "normal") {
  statusBanner.textContent = message;
  statusBanner.classList.remove("success", "danger");

  if (variant === "success") {
    statusBanner.classList.add("success");
  } else if (variant === "error") {
    statusBanner.classList.add("danger");
  }
}

function escapeHtml(text = "") {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString("zh-CN");
}

function formatMultilineText(text = "") {
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function autosizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
}

function hasPendingPromptEcho() {
  if (!state.pendingPrompt || !state.activeConversation?.messages?.length) {
    return false;
  }

  return state.activeConversation.messages.some(
    (message) =>
      message.role === "user" && String(message.content).trim() === state.pendingPrompt,
  );
}

function shouldPollBootstrap() {
  if (!state.user || state.isSending) {
    return false;
  }

  const hasRunningJob = state.jobs.some((job) =>
    ["queued", "processing", "running"].includes(String(job.status)),
  );
  const hasProcessingDocument = state.documents.some((document) =>
    ["queued", "processing"].includes(String(document.status)),
  );
  const hasRunningRun = state.runs.some((run) =>
    ["queued", "processing", "running"].includes(String(run.status)),
  );

  return hasRunningJob || hasProcessingDocument || hasRunningRun;
}

function normalizeSummaryHeading(rawHeading = "") {
  const heading = rawHeading.replaceAll("：", "").trim();
  const aliasMap = {
    Title: "标题",
    Authors: "作者",
    "One-sentence Summary": "一句话总结",
  };
  return aliasMap[heading] || heading;
}

function parseStructuredSummary(summary = "") {
  const normalized = String(summary).replace(/\r\n/g, "\n").trim();
  if (!normalized.includes("## ")) {
    return null;
  }

  const prepared = normalized.replace(/\s+(##\s+)/g, "\n$1").replace(/^\n+/, "");
  const sections = prepared
    .split(/\n(?=##\s+)/)
    .map((block) => block.trim())
    .filter(Boolean);

  if (!sections.length) {
    return null;
  }

  const parsed = {
    title: "",
    authors: "",
    lead: "",
    sections: [],
  };

  for (const block of sections) {
    const headingMatch = block.match(/^##\s+([^\n]+)\n?/);
    if (!headingMatch) {
      continue;
    }

    const heading = normalizeSummaryHeading(headingMatch[1]);
    const content = block.slice(headingMatch[0].length).trim();
    if (!content || heading === "Agent 可用信息") {
      continue;
    }

    if (heading === "标题") {
      parsed.title = content;
      continue;
    }

    if (heading === "作者") {
      parsed.authors = content;
      continue;
    }

    if (heading === "一句话总结") {
      parsed.lead = content.replace(/^- /gm, "").trim();
      continue;
    }

    parsed.sections.push({ heading, content });
  }

  if (!parsed.title && !parsed.authors && !parsed.lead && !parsed.sections.length) {
    return null;
  }

  return parsed;
}

function renderSummarySectionContent(content) {
  const lines = String(content)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const bulletLines = lines.filter((line) => line.startsWith("- "));
  const paragraphLines = lines.filter((line) => !line.startsWith("- "));
  const html = [];

  if (paragraphLines.length) {
    html.push(`<p class="summary-paragraph">${formatMultilineText(paragraphLines.join("\n"))}</p>`);
  }

  if (bulletLines.length) {
    html.push(`
      <ul class="summary-points">
        ${bulletLines
          .map((line) => `<li>${escapeHtml(line.replace(/^- /, ""))}</li>`)
          .join("")}
      </ul>
    `);
  }

  if (!html.length) {
    return `<p class="summary-paragraph">${formatMultilineText(content)}</p>`;
  }

  return html.join("");
}

function renderDocumentSummary(summary = "") {
  const parsed = parseStructuredSummary(summary);
  if (!parsed) {
    return `<div class="document-summary-plain">${formatMultilineText(summary || "摘要生成中...")}</div>`;
  }

  return `
    <div class="paper-summary">
      ${parsed.title ? `<h3 class="paper-summary-title">${escapeHtml(parsed.title)}</h3>` : ""}
      ${parsed.authors ? `<p class="paper-summary-authors">${escapeHtml(parsed.authors)}</p>` : ""}
      ${
        parsed.lead
          ? `
            <section class="summary-lede">
              <span class="summary-chip">一句话总结</span>
              <p>${escapeHtml(parsed.lead)}</p>
            </section>
          `
          : ""
      }
      <div class="summary-grid">
        ${parsed.sections
          .map(
            (section) => `
              <article class="summary-section">
                <h4>${escapeHtml(section.heading)}</h4>
                ${renderSummarySectionContent(section.content)}
              </article>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function getStatusLabel(status = "") {
  const labels = {
    queued: "排队中",
    processing: "处理中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
  };
  return labels[String(status)] || String(status || "未知");
}

function sumUsage(items = []) {
  return items.reduce(
    (acc, item) => ({
      promptTokens: acc.promptTokens + Number(item.promptTokens || 0),
      completionTokens: acc.completionTokens + Number(item.completionTokens || 0),
      totalTokens: acc.totalTokens + Number(item.totalTokens || 0),
      estimatedCost: acc.estimatedCost + Number(item.estimatedCost || 0),
    }),
    { promptTokens: 0, completionTokens: 0, totalTokens: 0, estimatedCost: 0 },
  );
}

function updateMetricCards() {
  conversationCount.textContent = String(state.conversations.length);
  documentCount.textContent = String(state.documents.length);
  jobCount.textContent = String(state.jobs.length);
  preferenceCount.textContent = String(state.preferences.length);
  toolCount.textContent = String(state.tools.length);
  runCount.textContent = String(state.runs.length);
}

function renderComposerState() {
  sendButton.disabled = !state.user || state.isSending;
  sendButton.textContent = state.isSending ? "发送中..." : "发送任务";
}

function renderConversationList() {
  if (!state.conversations.length) {
    conversationList.innerHTML = '<div class="empty-state">还没有会话，先发起一个任务吧。</div>';
    return;
  }

  conversationList.innerHTML = state.conversations
    .map(
      (conversation) => `
        <div class="conversation-item ${conversation.id === state.activeConversationId ? "active" : ""}">
          <button data-conversation-id="${conversation.id}">
            <strong>${escapeHtml(conversation.title || "新的任务")}</strong>
            <div class="mono">${formatTime(conversation.updatedAt)}</div>
          </button>
        </div>
      `,
    )
    .join("");

  for (const button of conversationList.querySelectorAll("button[data-conversation-id]")) {
    button.addEventListener("click", async () => {
      await loadConversation(button.dataset.conversationId);
    });
  }
}

function renderMessages() {
  const messages = [...(state.activeConversation?.messages ?? [])];

  if (state.pendingPrompt && !hasPendingPromptEcho()) {
    messages.push({
      id: "__pending_user__",
      role: "user",
      content: state.pendingPrompt,
      createdAt: new Date().toISOString(),
      pending: true,
    });
  }

  if (state.isSending) {
    messages.push({
      id: "__pending_agent__",
      role: "assistant",
      content: "Agent 正在读取上下文、规划动作并准备响应",
      createdAt: new Date().toISOString(),
      pending: true,
      typing: true,
    });
  }

  activeConversationLabel.textContent = state.activeConversation
    ? state.activeConversation.title || "当前会话"
    : state.pendingPrompt
      ? "正在创建会话..."
      : "未选择会话";

  if (!messages.length) {
    messageList.innerHTML =
      '<div class="empty-state">登录后可以直接提问，Agent 会结合工具、记忆、文档和模板来完成任务。</div>';
    return;
  }

  messageList.innerHTML = messages
    .map(
      (message) => `
        <article class="message ${message.role} ${message.pending ? "pending" : ""} ${message.typing ? "typing" : ""}">
          <span class="message-meta">${message.role === "user" ? "你" : "Agent"} · ${formatTime(message.createdAt)}</span>
          ${escapeHtml(message.content)}
        </article>
      `,
    )
    .join("");

  messageList.scrollTop = messageList.scrollHeight;
}

function renderDocuments() {
  if (!state.documents.length) {
    documentList.innerHTML =
      '<div class="empty-state">上传论文、Markdown 或代码文件后，这里会自动显示整理后的摘要。</div>';
    return;
  }

  documentList.innerHTML = state.documents
    .map(
      (document) => `
        <div class="document-item">
          <div class="document-item-header">
            <div class="document-item-meta">
              <strong>${escapeHtml(document.originalName)}</strong>
              <div class="mono">状态：${escapeHtml(getStatusLabel(document.status))}</div>
              <div class="mono">${formatTime(document.updatedAt)}</div>
            </div>
            <span class="tag">${escapeHtml((document.originalName || "").split(".").pop()?.toUpperCase() || "FILE")}</span>
          </div>
          ${renderDocumentSummary(document.summary || "摘要生成中...")}
          <div class="action-row">
            <button class="mini-button" data-document-process="${document.id}">重新处理</button>
          </div>
        </div>
      `,
    )
    .join("");

  for (const button of documentList.querySelectorAll("button[data-document-process]")) {
    button.addEventListener("click", async () => {
      try {
        setStatus("已提交文档重新处理请求...");
        await apiFetch(`/api/documents/${button.dataset.documentProcess}/process`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await refreshBootstrap();
        setStatus("文档已重新进入后台处理队列。", "success");
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  }
}

function renderJobs() {
  if (!state.jobs.length) {
    jobList.innerHTML =
      '<div class="empty-state">上传文档后，这里会显示后台摘要任务的执行状态。</div>';
    return;
  }

  jobList.innerHTML = state.jobs
    .map(
      (job) => `
        <div class="job-item">
          <strong>${escapeHtml(job.type)}</strong>
          <div class="mono">状态：${escapeHtml(getStatusLabel(job.status))}</div>
          <div class="mono">尝试次数：${escapeHtml(String(job.attemptCount || 0))}</div>
          <div>${escapeHtml(job.error || (job.output?.summary ? "摘要已生成，可在文档区查看。" : "等待处理结果..."))}</div>
          <div class="action-row">
            <button class="mini-button" data-job-retry="${job.id}">重试</button>
          </div>
        </div>
      `,
    )
    .join("");

  for (const button of jobList.querySelectorAll("button[data-job-retry]")) {
    button.addEventListener("click", async () => {
      try {
        setStatus("正在重试后台任务...");
        await apiFetch(`/api/jobs/${button.dataset.jobRetry}/retry`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await refreshBootstrap();
        setStatus("后台任务已重新入队。", "success");
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  }
}

function renderPreferences() {
  if (!state.preferences.length) {
    preferenceList.innerHTML =
      '<div class="empty-state">当你持续表达偏好后，长期记忆会在这里沉淀下来。</div>';
  } else {
    preferenceList.innerHTML = state.preferences
      .map(
        (item) => `
          <div class="preference-item">
            <strong>${escapeHtml(item.key)}</strong>
            <div>${escapeHtml(item.value)}</div>
            <div class="action-row">
              <span class="tag">${escapeHtml(item.source)}</span>
              <button class="mini-button" data-memory-delete="${item.id}">删除</button>
            </div>
          </div>
        `,
      )
      .join("");
  }

  for (const button of preferenceList.querySelectorAll("button[data-memory-delete]")) {
    button.addEventListener("click", async () => {
      try {
        await apiFetch(`/api/memory/${button.dataset.memoryDelete}`, {
          method: "DELETE",
        });
        await refreshBootstrap();
        setStatus("记忆项已删除。", "success");
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  }
}

function renderMemoryWritebacks() {
  if (!state.memoryWritebacks.length) {
    memoryWritebackList.innerHTML =
      '<div class="empty-state">运行完成后，这里会显示最近的记忆写回记录。</div>';
    return;
  }

  memoryWritebackList.innerHTML = state.memoryWritebacks
    .slice(0, 5)
    .map(
      (item) => `
        <div class="writeback-item">
          <div class="item-row">
            <strong>${escapeHtml(item.source || "writeback")}</strong>
            <span class="mono">${formatTime(item.updatedAt)}</span>
          </div>
          <div>${escapeHtml(item.summary || "已写回记忆")}</div>
        </div>
      `,
    )
    .join("");
}

function getSampleToolArgs(name) {
  const firstDocument = state.documents[0];
  const defaults = {
    list_uploaded_documents: {
      userId: state.user?.id ?? "",
      conversationId: state.activeConversationId ?? "",
      question: "列出当前会话已上传文档",
    },
    read_uploaded_document: {
      documentId: firstDocument?.id ?? "",
      question: "读取文档摘要",
    },
    inspect_github_repo: {
      repo: "openai/openai-python",
      question: "概览仓库亮点",
    },
    list_knowledge_templates: {
      question: "列出可用模板",
    },
    read_knowledge_template: {
      templateId: "resume-project",
      question: "读取简历项目模板",
    },
  };
  return defaults[name] ?? {};
}

function renderTools() {
  if (!state.tools.length) {
    toolList.innerHTML =
      '<div class="empty-state">工具目录还没有同步，点击“同步”按钮后会展示可用 MCP 工具。</div>';
    toolSelect.innerHTML = "";
    return;
  }

  toolList.innerHTML = state.tools
    .map(
      (tool) => `
        <div class="tool-item">
          <div class="item-row">
            <strong>${escapeHtml(tool.name)}</strong>
            <span class="tag">${escapeHtml(tool.serverName || "workspace-mcp-server")}</span>
          </div>
          <div>${escapeHtml(tool.description || "无描述")}</div>
          <pre class="tool-schema">${escapeHtml(JSON.stringify(tool.inputSchema || {}, null, 2))}</pre>
          <div class="action-row">
            <button class="mini-button" data-tool-fill="${tool.name}">填入示例参数</button>
          </div>
        </div>
      `,
    )
    .join("");

  toolSelect.innerHTML = state.tools
    .map((tool) => `<option value="${escapeHtml(tool.name)}">${escapeHtml(tool.name)}</option>`)
    .join("");

  if (toolSelect.value) {
    toolArguments.value = JSON.stringify(getSampleToolArgs(toolSelect.value), null, 2);
  }

  for (const button of toolList.querySelectorAll("button[data-tool-fill]")) {
    button.addEventListener("click", () => {
      const toolName = button.dataset.toolFill;
      toolSelect.value = toolName;
      toolArguments.value = JSON.stringify(getSampleToolArgs(toolName), null, 2);
      autosizeTextarea(toolArguments);
    });
  }
}

function renderRuns() {
  if (!state.runs.length) {
    runList.innerHTML =
      '<div class="empty-state">执行一次任务后，这里会显示运行记录、trace 和使用量。</div>';
    return;
  }

  runList.innerHTML = state.runs
    .map(
      (run) => `
        <div class="run-item ${run.id === state.activeRunId ? "active" : ""}">
          <button data-run-id="${run.id}">
            <strong>${escapeHtml(run.prompt?.slice(0, 40) || "任务运行")}</strong>
            <div class="mono">${escapeHtml(getStatusLabel(run.status))} · ${formatTime(run.updatedAt)}</div>
            <div class="mono">prompt ${escapeHtml(run.promptVersion || "-")}</div>
          </button>
        </div>
      `,
    )
    .join("");

  for (const button of runList.querySelectorAll("button[data-run-id]")) {
    button.addEventListener("click", async () => {
      await loadRun(button.dataset.runId);
    });
  }
}

function renderRunDetail() {
  activeRunLabel.textContent = state.activeRun
    ? `${getStatusLabel(state.activeRun.status)} · ${state.activeRun.promptVersion || "no-version"}`
    : "未选择运行";

  if (!state.activeRun) {
    runDetail.innerHTML =
      '<div class="empty-state">选择一条运行记录后，这里会显示步骤、工具日志、结构化输出和 token 使用量。</div>';
    return;
  }

  const usageItems = state.activeRun.usage ?? [];
  const usageTotal = sumUsage(usageItems);
  const steps = state.activeRun.steps ?? [];
  const toolLogs = state.activeRun.toolLogs ?? [];
  const structuredOutput = state.activeRun.structuredOutput;

  runDetail.innerHTML = `
    <div class="trace-card">
      <div class="trace-header">
        <div class="trace-meta">
          <strong>${escapeHtml(state.activeRun.prompt || "任务运行")}</strong>
          <div class="mono">创建于 ${formatTime(state.activeRun.createdAt)}</div>
        </div>
        <span class="tag">${escapeHtml(getStatusLabel(state.activeRun.status))}</span>
      </div>
      <div class="detail-metrics">
        <div class="detail-metric">
          <span>步骤</span>
          <strong>${steps.length}</strong>
        </div>
        <div class="detail-metric">
          <span>工具调用</span>
          <strong>${toolLogs.length}</strong>
        </div>
        <div class="detail-metric">
          <span>总 Token</span>
          <strong>${usageTotal.totalTokens}</strong>
        </div>
        <div class="detail-metric">
          <span>估算成本</span>
          <strong>${usageTotal.estimatedCost.toFixed(6)}</strong>
        </div>
      </div>
    </div>

    <div class="trace-card">
      <h4>最终回答</h4>
      <div class="trace-paragraph">${formatMultilineText(state.activeRun.result || "暂无结果")}</div>
    </div>

    <div class="trace-card">
      <h4>结构化输出</h4>
      <pre class="trace-pre">${escapeHtml(
        JSON.stringify(structuredOutput || state.activeRun.structuredOutput || {}, null, 2),
      )}</pre>
    </div>

    <div class="trace-card">
      <h4>执行步骤</h4>
      <ul class="trace-list">
        ${steps
          .map(
            (step) => `
              <li>
                <strong>${escapeHtml(step.name)}</strong>
                <span class="mono"> · ${escapeHtml(getStatusLabel(step.status))}</span>
                <div>${escapeHtml(step.error || "")}</div>
              </li>
            `,
          )
          .join("")}
      </ul>
    </div>

    <div class="trace-card">
      <h4>工具日志</h4>
      <ul class="trace-list">
        ${
          toolLogs.length
            ? toolLogs
                .map(
                  (item) => `
                    <li>
                      <strong>${escapeHtml(item.name)}</strong>
                      <span class="mono"> · ${escapeHtml(getStatusLabel(item.status))}</span>
                      <div>${escapeHtml(JSON.stringify(item.arguments || {}))}</div>
                    </li>
                  `,
                )
                .join("")
            : "<li>这次运行没有触发工具调用。</li>"
        }
      </ul>
    </div>

    <div class="trace-card">
      <h4>模型使用量</h4>
      <ul class="trace-list">
        ${
          usageItems.length
            ? usageItems
                .map(
                  (item) => `
                    <li>
                      <strong>${escapeHtml(item.stage)}</strong>
                      <span class="mono"> · ${escapeHtml(item.model || "-")}</span>
                      <div>prompt ${escapeHtml(String(item.promptTokens || 0))} / completion ${escapeHtml(String(item.completionTokens || 0))} / total ${escapeHtml(String(item.totalTokens || 0))}</div>
                    </li>
                  `,
                )
                .join("")
            : "<li>当前没有记录到 token 使用量。</li>"
        }
      </ul>
    </div>
  `;
}

function renderSession() {
  const loggedIn = Boolean(state.user);
  authPanel.classList.toggle("hidden", loggedIn);
  sessionPanel.classList.toggle("hidden", !loggedIn);
  authHint.textContent = loggedIn ? "已登录" : "请先登录";

  if (loggedIn) {
    userName.textContent = state.user.name || "未命名用户";
    userEmail.textContent = state.user.email;
    workspaceSelect.innerHTML = state.workspaces
      .map(
        (workspace) => `
          <option value="${escapeHtml(workspace.id)}" ${workspace.id === state.currentWorkspace?.id ? "selected" : ""}>
            ${escapeHtml(workspace.name)}
          </option>
        `,
      )
      .join("");
    currentWorkspaceRole.textContent = state.currentWorkspace?.currentRole || "viewer";
  } else {
    workspaceSelect.innerHTML = "";
    currentWorkspaceRole.textContent = "owner";
  }
}

function renderToolTestResult() {
  if (!state.toolTestResult) {
    toolTestResult.className = "tool-test-result empty-state";
    toolTestResult.textContent = "还没有测试结果。";
    return;
  }

  toolTestResult.className = "tool-test-result";
  toolTestResult.innerHTML = `<pre class="trace-pre">${escapeHtml(
    JSON.stringify(state.toolTestResult, null, 2),
  )}</pre>`;
}

function renderAll() {
  renderSession();
  renderComposerState();
  renderConversationList();
  renderMessages();
  renderDocuments();
  renderJobs();
  renderPreferences();
  renderMemoryWritebacks();
  renderTools();
  renderRuns();
  renderRunDetail();
  renderToolTestResult();
  updateMetricCards();
}

async function loadRun(runId, shouldRender = true) {
  if (!runId) {
    state.activeRunId = null;
    state.activeRun = null;
    if (shouldRender) {
      renderAll();
    }
    return;
  }

  state.activeRunId = runId;
  state.activeRun = await apiFetch(`/api/agent/runs/${runId}`);
  if (shouldRender) {
    renderAll();
  }
}

async function refreshBootstrap() {
  if (!state.user) {
    return;
  }

  const data = await apiFetch("/api/bootstrap");
  state.conversations = data.conversations;
  state.documents = data.documents;
  state.jobs = data.jobs;
  state.preferences = data.preferences;
  state.memoryWritebacks = data.memoryWritebacks;
  state.tools = data.tools;
  state.toolLogs = data.toolLogs;
  state.runs = data.runs;
  state.workspaces = data.workspaces ?? [];
  state.currentWorkspace = data.currentWorkspace ?? state.currentWorkspace;
  if (!state.currentWorkspace && state.workspaces[0]) {
    state.currentWorkspace = state.workspaces[0];
  }

  if (!state.activeConversationId && state.conversations[0]) {
    state.activeConversationId = state.conversations[0].id;
  }

  if (state.activeConversationId) {
    await loadConversation(state.activeConversationId, false);
  } else {
    state.activeConversation = null;
  }

  if (!state.activeRunId && state.runs[0]) {
    state.activeRunId = state.runs[0].id;
  }

  if (state.activeRunId) {
    const activeStillExists = state.runs.some((run) => run.id === state.activeRunId);
    if (activeStillExists) {
      await loadRun(state.activeRunId, false);
    } else {
      state.activeRunId = state.runs[0]?.id ?? null;
      if (state.activeRunId) {
        await loadRun(state.activeRunId, false);
      } else {
        state.activeRun = null;
      }
    }
  }

  renderAll();
}

async function loadConversation(conversationId, shouldRender = true) {
  state.activeConversationId = conversationId;
  state.activeConversation = await apiFetch(`/api/conversations/${conversationId}`);

  if (state.activeConversation?.runs?.length) {
    const newestRunId = state.activeConversation.runs[0]?.id;
    if (newestRunId) {
      state.activeRunId = newestRunId;
      await loadRun(newestRunId, false);
    }
  }

  if (shouldRender) {
    renderAll();
  }
}

async function checkSession() {
  state.user = await apiFetch("/api/me");

  if (state.user) {
    const workspacePayload = await apiFetch("/api/workspaces");
    state.workspaces = workspacePayload.workspaces ?? [];
    if (!state.currentWorkspace && state.workspaces[0]) {
      state.currentWorkspace = state.workspaces[0];
    }
    setStatus("已登录，可以直接发起任务、上传文档、同步工具并查看运行 trace。", "success");
    await refreshBootstrap();
  } else {
    setStatus("系统待命中，登录后即可开始使用。");
    renderAll();
  }
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);

  try {
    await apiFetch("/api/auth/sign-in/email", {
      method: "POST",
      body: JSON.stringify({
        email: formData.get("email"),
        password: formData.get("password"),
      }),
    });
    form.reset();
    await checkSession();
  } catch (error) {
    setStatus(error.message, "error");
  }
});

document.querySelector("#register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);

  try {
    await apiFetch("/api/auth/sign-up/email", {
      method: "POST",
      body: JSON.stringify({
        name: formData.get("name"),
        email: formData.get("email"),
        password: formData.get("password"),
      }),
    });
    form.reset();
    await checkSession();
  } catch (error) {
    setStatus(error.message, "error");
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  await apiFetch("/api/auth/sign-out", {
    method: "POST",
    body: JSON.stringify({}),
  });

  state.user = null;
  state.conversations = [];
  state.activeConversation = null;
  state.activeConversationId = null;
  state.documents = [];
  state.jobs = [];
  state.preferences = [];
  state.memoryWritebacks = [];
  state.tools = [];
  state.toolLogs = [];
  state.runs = [];
  state.workspaces = [];
  state.currentWorkspace = null;
  state.activeRunId = null;
  state.activeRun = null;
  state.pendingPrompt = "";
  state.isSending = false;
  state.toolTestResult = null;

  setStatus("已退出登录。");
  renderAll();
});

workspaceSelect.addEventListener("change", async () => {
  const workspace = state.workspaces.find((item) => item.id === workspaceSelect.value);
  if (!workspace) {
    return;
  }

  state.currentWorkspace = workspace;
  state.activeConversationId = null;
  state.activeConversation = null;
  state.activeRunId = null;
  state.activeRun = null;
  await refreshBootstrap();
  setStatus(`已切换到工作区：${workspace.name}`, "success");
});

document.querySelector("#new-workspace-button").addEventListener("click", async () => {
  if (!state.user) {
    return;
  }

  const name = window.prompt("请输入新工作区名称：", "New Workspace");
  if (!name?.trim()) {
    return;
  }

  try {
    const workspace = await apiFetch("/api/workspaces", {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    });
    state.currentWorkspace = workspace;
    const workspacePayload = await apiFetch("/api/workspaces");
    state.workspaces = workspacePayload.workspaces ?? [];
    state.activeConversationId = null;
    state.activeConversation = null;
    state.activeRunId = null;
    state.activeRun = null;
    await refreshBootstrap();
    setStatus(`工作区已创建：${workspace.name}`, "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

document.querySelector("#new-conversation-button").addEventListener("click", async () => {
  if (!state.user) {
    setStatus("请先登录。", "error");
    return;
  }

  const conversation = await apiFetch("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新的任务" }),
  });
  state.conversations.unshift(conversation);
  await loadConversation(conversation.id);
});

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!state.user) {
    setStatus("请先登录。", "error");
    return;
  }

  if (state.isSending) {
    return;
  }

  const draft = promptInput.value.trim();
  if (!draft) {
    return;
  }

  promptInput.value = "";
  autosizeTextarea(promptInput);
  promptInput.focus();

  state.isSending = true;
  state.pendingPrompt = draft;
  renderAll();
  setStatus("Agent 正在规划步骤，并可能调用工具处理你的任务...");

  try {
    const result = await apiFetch("/api/agent/run", {
      method: "POST",
      body: JSON.stringify({
        conversationId: state.activeConversationId,
        prompt: draft,
      }),
    });

    state.pendingPrompt = "";
    state.isSending = false;
    state.activeConversationId = result.conversationId;
    state.activeRunId = result.run?.id ?? state.activeRunId;

    await refreshBootstrap();
    setStatus("任务已完成，结果、trace 和长期记忆都已写回。", "success");
  } catch (error) {
    state.pendingPrompt = "";
    state.isSending = false;
    promptInput.value = draft;
    autosizeTextarea(promptInput);
    promptInput.focus();
    renderAll();
    setStatus(error.message, "error");
  }
});

document.querySelector("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!state.user) {
    setStatus("请先登录。", "error");
    return;
  }

  const fileInput = document.querySelector("#document-input");
  if (!fileInput.files?.length) {
    return;
  }

  const formData = new FormData();
  formData.append("document", fileInput.files[0]);
  if (state.activeConversationId) {
    formData.append("conversationId", state.activeConversationId);
  }

  try {
    setStatus("文档已上传，后台正在生成摘要...");
    const result = await apiFetch("/api/documents/upload", {
      method: "POST",
      body: formData,
      headers: {},
    });

    state.activeConversationId = result.conversationId;
    fileInput.value = "";
    await refreshBootstrap();

    const fallbackNote = result.job.queueFallbackReason ? "，已自动回退到本地队列" : "";
    setStatus(`文档已进入后台处理，当前队列：${result.job.queueProvider}${fallbackNote}`, "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

document.querySelector("#refresh-memory-button").addEventListener("click", async () => {
  if (!state.user) {
    return;
  }

  try {
    setStatus("正在刷新长期记忆快照...");
    await apiFetch("/api/memory/refresh", {
      method: "POST",
      body: JSON.stringify({ query: "总结当前长期记忆" }),
    });
    await refreshBootstrap();
    setStatus("长期记忆已刷新。", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

document.querySelector("#connect-tools-button").addEventListener("click", async () => {
  if (!state.user) {
    return;
  }

  try {
    setStatus("正在同步 MCP 工具目录...");
    await apiFetch("/api/tools/connect", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshBootstrap();
    setStatus("工具目录已同步。", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

toolSelect.addEventListener("change", () => {
  toolArguments.value = JSON.stringify(getSampleToolArgs(toolSelect.value), null, 2);
  autosizeTextarea(toolArguments);
});

document.querySelector("#tool-test-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!state.user) {
    return;
  }

  const toolName = toolSelect.value;
  if (!toolName) {
    setStatus("请先同步并选择一个工具。", "error");
    return;
  }

  let argumentsPayload = {};
  try {
    argumentsPayload = JSON.parse(toolArguments.value || "{}");
  } catch {
    setStatus("工具参数必须是合法 JSON。", "error");
    return;
  }

  try {
    setStatus(`正在测试工具 ${toolName} ...`);
    state.toolTestResult = await apiFetch("/api/tools/test", {
      method: "POST",
      body: JSON.stringify({
        name: toolName,
        arguments: argumentsPayload,
      }),
    });
    renderToolTestResult();
    setStatus(`工具 ${toolName} 测试完成。`, "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

promptInput.addEventListener("input", () => {
  autosizeTextarea(promptInput);
});

toolArguments.addEventListener("input", () => {
  autosizeTextarea(toolArguments);
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }
});

setInterval(async () => {
  if (!shouldPollBootstrap()) {
    return;
  }

  try {
    await refreshBootstrap();
  } catch {
    // Ignore transient polling errors and let the next loop retry.
  }
}, 8000);

autosizeTextarea(promptInput);
autosizeTextarea(toolArguments);
checkSession();
