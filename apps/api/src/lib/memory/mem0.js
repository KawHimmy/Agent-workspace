import { featureFlags, env } from "../../config/env.js";
import { listPreferencesByUser, upsertPreference } from "../storage/appStore.js";

const MEM0_BASE_URL = "https://api.mem0.ai";

function buildAuthHeaders() {
  return {
    Authorization: `Token ${env.mem0ApiKey}`,
    Accept: "application/json",
    "Content-Type": "application/json",
  };
}

function extractPreferenceCandidates(text) {
  const rules = [
    { key: "output_style", regex: /(简洁|详细|口语化|正式)/ },
    { key: "target_role", regex: /(前端|后端|全栈|AI|算法|产品|设计)/ },
    { key: "language", regex: /(中文|英文)/ },
  ];

  return rules
    .map((rule) => {
      const match = text.match(rule.regex);
      return match ? { key: rule.key, value: match[0] } : null;
    })
    .filter(Boolean);
}

export async function retrieveMemoryContext(userId, query) {
  const localPreferences = await listPreferencesByUser(userId);

  if (!featureFlags.hasMem0) {
    return {
      memoryText: localPreferences
        .map((item) => `${item.key}: ${item.value}`)
        .join("\n"),
      items: localPreferences,
      source: "local",
    };
  }

  try {
    const response = await fetch(`${MEM0_BASE_URL}/v2/memories/search/`, {
      method: "POST",
      headers: buildAuthHeaders(),
      body: JSON.stringify({
        query,
        filters: {
          OR: [{ user_id: userId }],
        },
        version: "v2",
        top_k: 6,
      }),
    });

    if (!response.ok) {
      throw new Error(`Mem0 search failed with ${response.status}`);
    }

    const items = await response.json();
    return {
      memoryText: items.map((item) => item.memory).join("\n"),
      items,
      source: "mem0",
    };
  } catch {
    return {
      memoryText: localPreferences
        .map((item) => `${item.key}: ${item.value}`)
        .join("\n"),
      items: localPreferences,
      source: "local-fallback",
    };
  }
}

export async function writeConversationMemory({
  userId,
  userMessage,
  assistantMessage,
}) {
  const candidates = extractPreferenceCandidates(`${userMessage}\n${assistantMessage}`);

  for (const candidate of candidates) {
    await upsertPreference({
      userId,
      key: candidate.key,
      value: candidate.value,
      source: featureFlags.hasMem0 ? "mem0+local" : "local",
    });
  }

  if (!featureFlags.hasMem0) {
    return { source: "local-only" };
  }

  try {
    const response = await fetch(`${MEM0_BASE_URL}/v1/memories/`, {
      method: "POST",
      headers: buildAuthHeaders(),
      body: JSON.stringify({
        user_id: userId,
        messages: [
          { role: "user", content: userMessage },
          { role: "assistant", content: assistantMessage },
        ],
        metadata: {
          channel: "react-agent-workspace",
          stage: "mvp",
        },
      }),
    });

    if (!response.ok) {
      throw new Error(`Mem0 add failed with ${response.status}`);
    }

    return {
      source: "mem0",
      result: await response.json(),
    };
  } catch {
    return { source: "local-fallback" };
  }
}
