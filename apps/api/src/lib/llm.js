import { HumanMessage, SystemMessage } from "@langchain/core/messages";
import { ChatOpenAI } from "@langchain/openai";
import { featureFlags, env } from "../config/env.js";

let cachedModel;

export function getModel() {
  if (!featureFlags.hasLLM) {
    return null;
  }

  if (!cachedModel) {
    cachedModel = new ChatOpenAI({
      model: env.glmModel,
      apiKey: env.glmApiKey,
      temperature: 0.2,
      configuration: {
        baseURL: env.glmBaseUrl,
      },
    });
  }

  return cachedModel;
}

export async function summarizeTextWithLLM(text) {
  const model = getModel();

  if (!model) {
    return null;
  }

  try {
    const response = await Promise.race([
      model.invoke([
        new SystemMessage(
          "你是一个文档摘要助手。请输出 3 段内容：1）一句话总结；2）3 条要点；3）建议如何在 agent 任务里使用这份文档。",
        ),
        new HumanMessage(text.slice(0, 12000)),
      ]),
      new Promise((_, reject) => {
        setTimeout(() => reject(new Error("LLM summary timeout")), 8000);
      }),
    ]);

    return typeof response.content === "string"
      ? response.content
      : JSON.stringify(response.content);
  } catch {
    return null;
  }
}
