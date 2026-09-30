// Pure helpers for the backend-for-frontend proxy. Kept framework-free so they are unit-tested.

export const ACCESS_COOKIE = "mg_at";
export const REFRESH_COOKIE = "mg_rt";
const SEGMENT = /^[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,127}$/;
export const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "x-request-id"] as const;
const FORWARDED_RESPONSE_HEADERS = ["content-type", "content-disposition", "retry-after", "x-request-id"] as const;

/** Builds the upstream URL for /api/mg/<segments> or returns null if the path is not a safe API path. */
export function upstreamUrl(base: string, segments: readonly string[], search: string): URL | null {
  if (segments.length === 0 || segments.length > 12) return null;
  for (const s of segments) {
    if (!SEGMENT.test(s) || s === "." || s === ".." || s.includes("..")) return null;
  }
  let origin: URL;
  try {
    origin = new URL(base);
  } catch {
    return null;
  }
  const url = new URL(`/api/${segments.map(encodeURIComponent).join("/")}`, origin);
  if (url.origin !== origin.origin || !url.pathname.startsWith("/api/")) return null;
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  url.search = params.toString();
  return url;
}

/** Same-origin check for state-changing requests (CSRF defence in depth on top of SameSite=Strict cookies). */
export function isSameOrigin(method: string, originHeader: string | null, secFetchSite: string | null, host: string | null): boolean {
  if (method === "GET" || method === "HEAD") return true;
  if (secFetchSite !== null) return secFetchSite === "same-origin";
  if (!originHeader || !host) return false;
  try {
    return new URL(originHeader).host === host;
  } catch {
    return false;
  }
}

export function forwardRequestHeaders(incoming: Headers, accessToken: string | null): Headers {
  const out = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value) out.set(name, value);
  }
  if (accessToken) out.set("authorization", `Bearer ${accessToken}`);
  return out;
}

export function forwardResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.get(name);
    if (value) out.set(name, value);
  }
  out.set("cache-control", "no-store");
  return out;
}

export interface CookieOptions { httpOnly: true; secure: boolean; sameSite: "strict"; path: string; maxAge: number }

export function cookieOptions(kind: "access" | "refresh", maxAgeSeconds: number, secure: boolean): CookieOptions {
  return { httpOnly: true, secure, sameSite: "strict", path: kind === "access" ? "/" : "/api", maxAge: Math.max(0, Math.floor(maxAgeSeconds)) };
}

/** Fallback only; matches the backend default REFRESH_TOKEN_TTL_DAYS=14. */
export const REFRESH_MAX_AGE_SECONDS = 14 * 24 * 3600;
