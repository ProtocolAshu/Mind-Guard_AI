"""End-to-end check of the web backend-for-frontend against a running API (see scripts/run_e2e.sh)."""
import json, re, sys, time, uuid
import httpx

WEB = "http://127.0.0.1:3000"
SAME = {"origin": WEB, "sec-fetch-site": "same-origin"}
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))

def cookies_from(resp):
    jar = {}
    for raw in resp.headers.get_list("set-cookie"):
        name, _, rest = raw.partition("=")
        value = rest.split(";", 1)[0]
        jar[name] = (value, raw)
    return jar

c = httpx.Client(timeout=30)
r = c.get(f"{WEB}/login")
check("login page renders", r.status_code == 200 and "Sign in to MindGuard" in r.text, r.status_code)
check("CSP header on pages", "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""))

email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
r = c.post(f"{WEB}/api/session/register", json={"email": email, "password": "correct horse 42 battery", "timezone": "Asia/Kolkata"}, headers=SAME)
jar = cookies_from(r)
check("register via BFF", r.status_code == 201, r.text[:200])
check("no tokens in response body", "access_token" not in r.text and "refresh_token" not in r.text)
check("httpOnly SameSite=Strict session cookies", all(k in jar and "httponly" in jar[k][1].lower() and "samesite=strict" in jar[k][1].lower() for k in ("mg_at", "mg_rt")), list(jar))
check("refresh cookie scoped to /api", "path=/api" in jar["mg_rt"][1].lower())
at, rt = jar["mg_at"][0], jar["mg_rt"][0]
auth = {"cookie": f"mg_at={at}; mg_rt={rt}"}

r = c.get(f"{WEB}/api/mg/auth/me", headers=auth)
check("proxy GET with cookie auth", r.status_code == 200 and r.json()["email"] == email, r.text[:200])
check("proxy responses not cacheable", r.headers.get("cache-control") == "no-store")

r = c.put(f"{WEB}/api/mg/settings/consents/usage_monitoring", json={"granted": True}, headers={**auth, "origin": "http://evil.example", "sec-fetch-site": "cross-site"})
check("cross-site write rejected (CSRF)", r.status_code == 403 and r.json()["error"]["code"] == "csrf_rejected", r.text[:200])
r = c.put(f"{WEB}/api/mg/settings/consents/usage_monitoring", json={"granted": True}, headers={**auth, **SAME})
check("same-origin write accepted", r.status_code == 200 and r.json()["granted"] is True, r.text[:200])

for path in ("..%2F..%2Fhealth", "%2e%2e/admin/stats"):
    r = c.get(f"{WEB}/api/mg/{path}", headers=auth)
    check(f"traversal blocked: {path}", r.status_code in (400, 404) and "database=" not in r.text, f"{r.status_code} {r.text[:120]}")

r = c.post(f"{WEB}/api/mg/policies", json={"text": "Block Instagram strictly after 8 PM."}, headers={**auth, **SAME})
check("create policy through proxy", r.status_code == 201, r.text[:200])
now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
r = c.post(f"{WEB}/api/mg/events", json={"events": [{"client_event_id": f"e2e-{uuid.uuid4().hex[:12]}", "event_type": "APP_OPENED", "occurred_at": now, "app_package": "com.instagram.android", "payload": {}}]}, headers={**auth, **SAME})
check("ingest events through proxy", r.status_code == 200 and r.json()["accepted"] == 1, r.text[:200])
r = c.post(f"{WEB}/api/mg/interventions/evaluate", json={"app_package": "com.instagram.android", "session_minutes": 20}, headers={**auth, **SAME})
check("evaluate through proxy", r.status_code == 200 and r.json()["decision"] in ("ALLOW", "SOFT_WARNING", "MINDFUL_PROMPT", "DELAY", "LIMITED_ACCESS", "TEMPORARY_BLOCK", "REQUEST_CONFIRMATION", "FOCUS_MODE"), r.text[:300])

r = c.get(f"{WEB}/api/mg/analytics/daily", headers={"cookie": f"mg_rt={rt}"})
rotated = cookies_from(r)
check("refresh-on-missing-access rotates tokens", r.status_code == 200 and "mg_rt" in rotated and rotated["mg_rt"][0] != rt, f"{r.status_code} {list(rotated)}")
new_at, new_rt = rotated.get("mg_at", ("", ""))[0], rotated.get("mg_rt", ("", ""))[0]
r = c.get(f"{WEB}/api/mg/analytics/daily", headers={"cookie": f"mg_rt={rt}"})
check("reused refresh token rejected", r.status_code == 401, r.status_code)
r = c.get(f"{WEB}/api/mg/auth/me", headers={"cookie": f"mg_at={new_at}; mg_rt={new_rt}"})
check("reuse revoked the rotated family access still valid until expiry", r.status_code == 200, r.status_code)
r = c.get(f"{WEB}/api/mg/analytics/daily", headers={"cookie": f"mg_rt={new_rt}"})
check("rotated refresh token also revoked after reuse (family revocation)", r.status_code == 401, r.status_code)

email2 = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
r = c.post(f"{WEB}/api/session/register", json={"email": email2, "password": "correct horse 42 battery"}, headers=SAME)
jar2 = cookies_from(r)
auth2 = {"cookie": f"mg_at={jar2['mg_at'][0]}; mg_rt={jar2['mg_rt'][0]}"}
r = c.get(f"{WEB}/api/mg/user/export", headers=auth2)
check("export download via proxy", r.status_code == 200 and "attachment" in r.headers.get("content-disposition", "") and "password_hash" not in r.text, r.status_code)
r = c.post(f"{WEB}/api/session/logout", headers={**auth2, **SAME})
cleared = cookies_from(r)
check("logout clears cookies", r.status_code == 204 and cleared.get("mg_at", ("x",))[0] == "" and "max-age=0" in cleared["mg_rt"][1].lower(), f"{r.status_code} {cleared}")
r = c.get(f"{WEB}/api/mg/analytics/daily", headers={"cookie": f"mg_rt={jar2['mg_rt'][0]}"})
check("refresh token unusable after logout", r.status_code == 401, r.status_code)
r = c.get(f"{WEB}/dashboard")
check("dashboard shell renders", r.status_code == 200 and "MindGuard" in r.text, r.status_code)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
