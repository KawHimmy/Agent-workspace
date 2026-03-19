import fs from "node:fs/promises";
import path from "node:path";

const TEXT_FILE_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".csv",
  ".js",
  ".ts",
  ".html",
  ".css",
]);

function makeFallbackSummary(text) {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.slice(0, 220) || "文件已上传，但暂时无法提取更多文本内容。";
}

export async function extractDocumentText(filePath, originalName) {
  const extension = path.extname(originalName).toLowerCase();

  if (!TEXT_FILE_EXTENSIONS.has(extension)) {
    return {
      text: `文件名: ${originalName}\n说明: 当前 MVP 仅对文本类文件做深度提取，非文本文件先保存元数据并等待后续扩展解析器。`,
      isFallback: true,
    };
  }

  const buffer = await fs.readFile(filePath);
  const text = buffer.toString("utf8");

  return {
    text,
    isFallback: false,
  };
}

export function buildPlainSummary(text) {
  return makeFallbackSummary(text);
}
