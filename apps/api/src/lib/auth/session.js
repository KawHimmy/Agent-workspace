import { auth } from "./auth.js";

export async function getSessionFromRequest(req) {
  return auth.api.getSession({
    headers: new Headers(req.headers),
  });
}

export async function requireSession(req, res) {
  const session = await getSessionFromRequest(req);

  if (!session?.user) {
    res.status(401).json({
      error: "请先登录后再继续操作。",
    });
    return null;
  }

  return session;
}
