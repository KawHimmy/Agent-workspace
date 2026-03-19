import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "../../../../");
const dataDir = path.join(rootDir, "data");
const uploadsDir = path.join(rootDir, "storage", "uploads");
const storeFile = path.join(dataDir, "app-store.json");

const TEXT_EXTENSIONS = new Set([
  ".env",
  ".txt",
  ".md",
  ".json",
  ".js",
  ".mjs",
  ".cjs",
  ".ts",
  ".py",
  ".toml",
  ".yaml",
  ".yml",
]);

function walkTextFiles(startDir, results = [], depth = 0) {
  if (!fs.existsSync(startDir) || depth > 4) {
    return results;
  }

  for (const entry of fs.readdirSync(startDir, { withFileTypes: true })) {
    const fullPath = path.join(startDir, entry.name);

    if (entry.isDirectory()) {
      walkTextFiles(fullPath, results, depth + 1);
      continue;
    }

    if (!TEXT_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
      continue;
    }

    results.push(fullPath);
  }

  return results;
}

function discoverSecret(regex, directories) {
  for (const directory of directories) {
    const files = walkTextFiles(directory);

    for (const filePath of files) {
      try {
        const content = fs.readFileSync(filePath, "utf8");
        const match = content.match(regex);

        if (match?.[0]) {
          return match[0];
        }
      } catch {
        // 跳过无法读取的文件，避免开发时因为示例文件或二进制内容中断启动。
      }
    }
  }

  return undefined;
}

const allowAutoDiscovery = (process.env.ALLOW_AUTO_DISCOVERY ?? "true") !== "false";
const discoveryDirectories = [
  path.join(rootDir, "langgraph入门"),
  path.join(rootDir, "Mem0"),
];

const discoveredSecrets = allowAutoDiscovery
  ? {
      glmApiKey: discoverSecret(/\b[a-f0-9]{32}\.[A-Za-z0-9]+\b/i, discoveryDirectories),
      mem0ApiKey: discoverSecret(/\bm0-[A-Za-z0-9]+\b/, [path.join(rootDir, "Mem0")]),
      langsmithApiKey: discoverSecret(/\blsv2_pt_[A-Za-z0-9_]+\b/, [
        path.join(rootDir, "langgraph入门"),
      ]),
    }
  : {};

export const env = {
  port: Number(process.env.PORT ?? 3000),
  appUrl: process.env.APP_URL ?? "http://localhost:3000",
  betterAuthSecret: process.env.BETTER_AUTH_SECRET ?? "dev-only-better-auth-secret",
  glmModel: process.env.GLM_MODEL ?? "glm-4.7",
  glmBaseUrl: process.env.GLM_BASE_URL ?? "https://open.bigmodel.cn/api/paas/v4/",
  glmApiKey: process.env.GLM_API_KEY ?? discoveredSecrets.glmApiKey,
  mem0ApiKey: process.env.MEM0_API_KEY ?? discoveredSecrets.mem0ApiKey,
  langsmithApiKey: process.env.LANGSMITH_API_KEY ?? discoveredSecrets.langsmithApiKey,
  databaseUrl: process.env.DATABASE_URL,
  supabaseUrl: process.env.SUPABASE_URL,
  supabaseServiceRoleKey: process.env.SUPABASE_SERVICE_ROLE_KEY,
  rootDir,
  dataDir,
  uploadsDir,
  storeFile,
};

export const featureFlags = {
  hasLLM: Boolean(env.glmApiKey),
  hasMem0: Boolean(env.mem0ApiKey),
  hasSupabase: Boolean(env.supabaseUrl && env.supabaseServiceRoleKey),
};

