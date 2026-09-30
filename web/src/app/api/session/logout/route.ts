import { NextResponse, type NextRequest } from "next/server";
import { REFRESH_COOKIE, isSameOrigin } from "@/lib/proxy";
import { apiBase, clearSessionCookies, secureCookies } from "@/lib/session";

export async function POST(req: NextRequest) {
  if (!isSameOrigin("POST", req.headers.get("origin"), req.headers.get("sec-fetch-site"), req.headers.get("host"))) {
    return NextResponse.json({ error: { code: "csrf_rejected", message: "Cross-site request rejected.", details: null, request_id: null } }, { status: 403 });
  }
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  if (refresh) {
    await fetch(`${apiBase()}/api/auth/logout`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: refresh }), cache: "no-store",
    }).catch(() => undefined);
  }
  const res = new NextResponse(null, { status: 204 });
  clearSessionCookies(res, secureCookies(req));
  return res;
}
