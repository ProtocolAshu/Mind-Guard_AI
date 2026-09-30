import { NextResponse, type NextRequest } from "next/server";
import { ACCESS_COOKIE, REFRESH_COOKIE, REFRESH_MAX_AGE_SECONDS, cookieOptions } from "./proxy";

export function apiBase(): string {
  return process.env.MINDGUARD_API_URL ?? "http://localhost:8000";
}

export function secureCookies(req: NextRequest): boolean {
  return req.nextUrl.protocol === "https:" || process.env.NODE_ENV === "production";
}

interface TokenPayload { access_token: string; refresh_token: string; expires_in: number; refresh_expires_in?: number }

export function isTokenPayload(value: unknown): value is TokenPayload {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.access_token === "string" && typeof v.refresh_token === "string" && typeof v.expires_in === "number";
}

export function setSessionCookies(res: NextResponse, tokens: TokenPayload, secure: boolean): void {
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, cookieOptions("access", tokens.expires_in, secure));
  // The cookie never outlives the backend refresh token (REFRESH_TOKEN_TTL_DAYS); the constant is only a fallback.
  res.cookies.set(REFRESH_COOKIE, tokens.refresh_token, cookieOptions("refresh", tokens.refresh_expires_in ?? REFRESH_MAX_AGE_SECONDS, secure));
}

export function clearSessionCookies(res: NextResponse, secure: boolean): void {
  res.cookies.set(ACCESS_COOKIE, "", cookieOptions("access", 0, secure));
  res.cookies.set(REFRESH_COOKIE, "", cookieOptions("refresh", 0, secure));
}

export async function refreshTokens(refreshToken: string): Promise<TokenPayload | null> {
  const r = await fetch(`${apiBase()}/api/auth/refresh`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: refreshToken }), cache: "no-store",
  });
  if (!r.ok) return null;
  const body: unknown = await r.json();
  return isTokenPayload(body) ? body : null;
}

/** Login and registration share the same shape: forward credentials, keep tokens server-side, return the user. */
export async function credentialExchange(req: NextRequest, path: "/api/auth/login" | "/api/auth/register"): Promise<NextResponse> {
  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ error: { code: "invalid_json", message: "Request body must be JSON.", details: null, request_id: null } }, { status: 400 });
  }
  const upstream = await fetch(`${apiBase()}${path}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload), cache: "no-store",
  });
  const body: unknown = await upstream.json().catch(() => null);
  if (!upstream.ok || !isTokenPayload(body)) {
    return NextResponse.json(body ?? { error: { code: "upstream_error", message: "Sign-in failed.", details: null, request_id: null } },
      { status: upstream.ok ? 502 : upstream.status });
  }
  const res = NextResponse.json({ user: (body as unknown as { user: unknown }).user }, { status: upstream.status });
  setSessionCookies(res, body, secureCookies(req));
  return res;
}
