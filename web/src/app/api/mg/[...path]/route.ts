import { NextResponse, type NextRequest } from "next/server";
import {
  ACCESS_COOKIE, ALLOWED_METHODS, REFRESH_COOKIE, forwardRequestHeaders, forwardResponseHeaders, isSameOrigin, upstreamUrl,
} from "@/lib/proxy";
import { apiBase, refreshTokens, secureCookies, setSessionCookies } from "@/lib/session";

type Ctx = { params: Promise<{ path: string[] }> };

function problem(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message, details: null, request_id: null } }, { status });
}

async function handle(req: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const method = req.method.toUpperCase();
  if (!ALLOWED_METHODS.has(method)) return problem(405, "method_not_allowed", "Method not allowed.");
  if (!isSameOrigin(method, req.headers.get("origin"), req.headers.get("sec-fetch-site"), req.headers.get("host"))) {
    return problem(403, "csrf_rejected", "Cross-site request rejected.");
  }
  const { path } = await ctx.params;
  const target = upstreamUrl(apiBase(), path, req.nextUrl.search);
  if (!target) return problem(400, "invalid_path", "Invalid API path.");
  const body = method === "GET" || method === "DELETE" ? undefined : await req.arrayBuffer();
  let access = req.cookies.get(ACCESS_COOKIE)?.value ?? null;
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value ?? null;
  let rotated: Awaited<ReturnType<typeof refreshTokens>> = null;

  const send = () => fetch(target, { method, headers: forwardRequestHeaders(req.headers, access), body, cache: "no-store", redirect: "manual" });
  let upstream: Response;
  try {
    if (!access && refresh) {
      rotated = await refreshTokens(refresh);
      access = rotated?.access_token ?? null;
    }
    upstream = await send();
    if (upstream.status === 401 && refresh && !rotated) {
      rotated = await refreshTokens(refresh);
      if (rotated) {
        access = rotated.access_token;
        upstream = await send();
      }
    }
  } catch {
    return problem(502, "api_unreachable", "MindGuard API is unreachable. Check that the backend is running.");
  }
  const res = new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), {
    status: upstream.status, headers: forwardResponseHeaders(upstream.headers),
  });
  if (rotated) setSessionCookies(res, rotated, secureCookies(req));
  return res;
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
