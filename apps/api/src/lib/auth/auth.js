import { Pool } from "pg";
import { betterAuth } from "better-auth";
import { env } from "../../config/env.js";

const database = env.databaseUrl
  ? new Pool({
      connectionString: env.databaseUrl,
      ssl: env.databaseUrl.includes("supabase.co")
        ? { rejectUnauthorized: false }
        : undefined,
    })
  : undefined;

export const auth = betterAuth({
  database,
  secret: env.betterAuthSecret,
  baseURL: env.appUrl,
  trustedOrigins: [env.appUrl],
  emailAndPassword: {
    enabled: true,
    autoSignIn: true,
  },
});
