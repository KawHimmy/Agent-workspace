import express from "express";
import { Pool } from "pg";
import { betterAuth } from "better-auth";
import { toNodeHandler } from "better-auth/node";
import { tasks } from "@trigger.dev/sdk/v3";

const authPort = Number(process.env.AUTH_PORT ?? 3001);
const appUrl = process.env.APP_URL ?? "http://localhost:3000";
const databaseUrl = process.env.DATABASE_URL;
const internalServiceSecret =
  process.env.INTERNAL_SERVICE_SECRET ?? "dev-internal-secret";

const database = databaseUrl
  ? new Pool({
      connectionString: databaseUrl,
      ssl: databaseUrl.includes("supabase.co")
        ? { rejectUnauthorized: false }
        : undefined,
    })
  : undefined;

const auth = betterAuth({
  database,
  secret: process.env.BETTER_AUTH_SECRET ?? "dev-only-better-auth-secret",
  baseURL: appUrl,
  trustedOrigins: [appUrl],
  emailAndPassword: {
    enabled: true,
    autoSignIn: true,
  },
});

const app = express();
app.use(express.json({ limit: "1mb" }));

function hasValidInternalSecret(req) {
  return req.headers["x-internal-secret"] === internalServiceSecret;
}

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/", (_req, res) => {
  res.json({
    ok: true,
    service: "better-auth-sidecar",
    health: "/health",
    authBase: "/api/auth",
  });
});

app.post("/internal/trigger/document-summary", async (req, res) => {
  if (!hasValidInternalSecret(req)) {
    res.status(403).json({ error: "Forbidden" });
    return;
  }

  try {
    // The sidecar only dispatches the job. Python remains the summary executor.
    const handle = await tasks.trigger("document-summary", req.body);
    res.status(202).json({
      ok: true,
      handle,
    });
  } catch (error) {
    res.status(500).json({
      error: error instanceof Error ? error.message : "Trigger dispatch failed",
    });
  }
});

app.all("/api/auth/{*authPath}", toNodeHandler(auth));

app.listen(authPort, () => {
  console.log(`Better Auth sidecar is running at http://127.0.0.1:${authPort}`);
});
