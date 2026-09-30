import { describe, expect, it } from "vitest";
import { cookieOptions, forwardRequestHeaders, forwardResponseHeaders, isSameOrigin, upstreamUrl } from "@/lib/proxy";

describe("upstreamUrl", () => {
  const base = "http://api.internal:8000";
  it("maps safe segments under /api", () => {
    expect(upstreamUrl(base, ["analytics", "daily"], "?date=2026-09-14")?.toString()).toBe("http://api.internal:8000/api/analytics/daily?date=2026-09-14");
  });
  it("rejects traversal, empty, encoded and absolute segments", () => {
    expect(upstreamUrl(base, [], "")).toBeNull();
    expect(upstreamUrl(base, ["..", "admin"], "")).toBeNull();
    expect(upstreamUrl(base, ["goals", "%2e%2e"], "")).toBeNull();
    expect(upstreamUrl(base, ["http:", "", "evil.com"], "")).toBeNull();
    expect(upstreamUrl(base, ["a/b"], "")).toBeNull();
    expect(upstreamUrl(base, ["x".repeat(200)], "")).toBeNull();
  });
  it("never changes origin even with odd query strings", () => {
    const url = upstreamUrl(base, ["goals"], "?next=//evil.com&x=1");
    expect(url?.origin).toBe("http://api.internal:8000");
    expect(url?.searchParams.get("next")).toBe("//evil.com");
  });
});

describe("isSameOrigin", () => {
  it("allows reads", () => expect(isSameOrigin("GET", null, null, null)).toBe(true));
  it("uses Sec-Fetch-Site when present", () => {
    expect(isSameOrigin("POST", "https://app.example", "same-origin", "app.example")).toBe(true);
    expect(isSameOrigin("POST", "https://app.example", "cross-site", "app.example")).toBe(false);
  });
  it("falls back to Origin vs Host", () => {
    expect(isSameOrigin("DELETE", "https://app.example", null, "app.example")).toBe(true);
    expect(isSameOrigin("DELETE", "https://evil.example", null, "app.example")).toBe(false);
    expect(isSameOrigin("PATCH", null, null, "app.example")).toBe(false);
  });
});

describe("headers and cookies", () => {
  it("forwards only allow-listed request headers and injects the bearer token", () => {
    const h = forwardRequestHeaders(new Headers({ "content-type": "application/json", cookie: "mg_at=secret", authorization: "Bearer spoofed" }), "tok");
    expect(h.get("cookie")).toBeNull();
    expect(h.get("authorization")).toBe("Bearer tok");
  });
  it("strips upstream cookies and disables caching", () => {
    const h = forwardResponseHeaders(new Headers({ "set-cookie": "x=1", "content-type": "application/json" }));
    expect(h.get("set-cookie")).toBeNull();
    expect(h.get("cache-control")).toBe("no-store");
  });
  it("scopes cookies", () => {
    expect(cookieOptions("refresh", 10, true)).toEqual({ httpOnly: true, secure: true, sameSite: "strict", path: "/api", maxAge: 10 });
    expect(cookieOptions("access", -5, false).maxAge).toBe(0);
  });
});
