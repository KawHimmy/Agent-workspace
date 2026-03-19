const state = {
  user: null,
  conversations: [],
  activeConversationId: null,
  activeConversation: null,
  documents: [],
  jobs: [],
  preferences: [],
  isSending: false,
  pendingPrompt: "",
};

const authPanel = document.querySelector("#auth-panel");
const sessionPanel = document.querySelector("#session-panel");
const authHint = document.querySelector("#auth-hint");
const userName = document.querySelector("#user-name");
const userEmail = document.querySelector("#user-email");
const statusBanner = document.querySelector("#status-banner");
const conversationList = document.querySelector("#conversation-list");
const messageList = document.querySelector("#message-list");
const documentList = document.querySelector("#document-list");
const jobList = document.querySelector("#job-list");
const preferenceList = document.querySelector("#preference-list");
const activeConversationLabel = document.querySelector("#active-conversation-label");
const promptInput = document.querySelector("#prompt-input");
const sendButton = document.querySelector("#send-button");
const conversationCount = document.querySelector("#conversation-count");
const documentCount = document.querySelector("#document-count");
const jobCount = document.querySelector("#job-count");
const preferenceCount = document.querySelector("#preference-count");

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    credentials: "include",
    headers: {
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
    throw new Error(payload?.error || payload?.message || payload || "请求失败");
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

  return hasRunningJob || hasProcessingDocument;
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

  const prepared = normalized
    .replace(/\s+(##\s+)/g, "\n$1")
    .replace(/^\n+/, "");

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

function updateMetricCards() {
  conversationCount.textContent = String(state.conversations.length);
  documentCount.textContent = String(state.documents.length);
  jobCount.textContent = String(state.jobs.length);
  preferenceCount.textContent = String(state.preferences.length);
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
      content: "Agent 正在整理上下文并准备响应",
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
      '<div class="empty-state">登录后可以直接提问，Agent 会结合工具、记忆和文档来完成任务。</div>';
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
            </div>
            <span class="tag">${escapeHtml((document.originalName || "").split(".").pop()?.toUpperCase() || "FILE")}</span>
          </div>
          ${renderDocumentSummary(document.summary || "摘要生成中...")}
        </div>
      `,
    )
    .join("");
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
          <div>${escapeHtml(job.error || (job.output?.summary ? "摘要已生成，可在文档区查看。" : "等待处理结果..."))}</div>
          ${job.queueProvider ? `<span class="tag">${escapeHtml(job.queueProvider)}</span>` : ""}
        </div>
      `,
    )
    .join("");
}

function renderPreferences() {
  if (!state.preferences.length) {
    preferenceList.innerHTML =
      '<div class="empty-state">当你持续表达偏好后，长期记忆会在这里沉淀下来。</div>';
    return;
  }

  preferenceList.innerHTML = state.preferences
    .map(
      (item) => `
        <div class="preference-item">
          <strong>${escapeHtml(item.key)}</strong>
          <div>${escapeHtml(item.value)}</div>
          <span class="tag">${escapeHtml(item.source)}</span>
        </div>
      `,
    )
    .join("");
}

function renderSession() {
  const loggedIn = Boolean(state.user);
  authPanel.classList.toggle("hidden", loggedIn);
  sessionPanel.classList.toggle("hidden", !loggedIn);
  authHint.textContent = loggedIn ? "已登录" : "请先登录";

  if (loggedIn) {
    userName.textContent = state.user.name || "未命名用户";
    userEmail.textContent = state.user.email;
  }
}

function renderAll() {
  renderSession();
  renderComposerState();
  renderConversationList();
  renderMessages();
  renderDocuments();
  renderJobs();
  renderPreferences();
  updateMetricCards();
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

  if (!state.activeConversationId && state.conversations[0]) {
    state.activeConversationId = state.conversations[0].id;
  }

  if (state.activeConversationId) {
    await loadConversation(state.activeConversationId, false);
  } else {
    state.activeConversation = null;
  }

  renderAll();
}

async function loadConversation(conversationId, shouldRender = true) {
  state.activeConversationId = conversationId;
  state.activeConversation = await apiFetch(`/api/conversations/${conversationId}`);

  if (shouldRender) {
    renderAll();
  }
}

async function checkSession() {
  state.user = await apiFetch("/api/me");

  if (state.user) {
    setStatus("已登录，可以直接发起任务或上传文档。", "success");
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
  state.pendingPrompt = "";
  state.isSending = false;

  setStatus("已退出登录。");
  renderAll();
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
  setStatus("Agent 正在思考，并可能调用工具处理你的任务...");

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

    await refreshBootstrap();
    setStatus("任务已完成，结果已写回会话与长期记忆。", "success");
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

promptInput.addEventListener("input", () => {
  autosizeTextarea(promptInput);
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
checkSession();
