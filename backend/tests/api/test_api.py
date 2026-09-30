from datetime import timedelta

from tests.helpers import PASSWORD, grant, register

IG = "com.instagram.android"


def ev(cid, event_type, at, app=None, payload=None):
    return {"client_event_id": cid, "event_type": event_type, "occurred_at": at.isoformat(), "app_package": app, "payload": payload or {}}


async def test_openapi_health_ready_and_metrics(client):
    spec = (await client.get("/openapi.json")).json()
    for path in ("/api/goals", "/api/policies", "/api/events", "/api/interventions/evaluate", "/api/interventions/feedback",
                 "/api/analytics/daily", "/api/analytics/weekly", "/api/agent/runs", "/api/user/data", "/api/models/status"):
        assert path in spec["paths"], path
    assert (await client.get("/health")).json()["status"] == "ok"
    ready = await client.get("/ready")
    assert ready.status_code == 200 and "database=ok" in ready.text
    metrics = await client.get("/metrics")
    assert metrics.status_code == 200 and "mindguard_http_requests_total" in metrics.text
    assert (await client.get("/health")).headers["x-content-type-options"] == "nosniff"


async def test_auth_flow_register_login_refresh_rotation_logout(client):
    headers, _ = await register(client, "alice@example.com")
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["email"] == "alice@example.com"
    assert (await client.post("/api/auth/register", json={"email": "ALICE@example.com", "password": PASSWORD})).status_code == 409
    weak = await client.post("/api/auth/register", json={"email": "weak@example.com", "password": "short"})
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
    bad = await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong password 1"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    login = await client.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert login.status_code == 200
    refreshed = await client.post("/api/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})
    assert refreshed.status_code == 200 and refreshed.json()["refresh_token"] != login.json()["refresh_token"]
    out = await client.post("/api/auth/logout", json={"refresh_token": refreshed.json()["refresh_token"]})
    assert out.status_code == 204
    again = await client.post("/api/auth/refresh", json={"refresh_token": refreshed.json()["refresh_token"]})
    assert again.status_code == 401


async def test_goals_crud(client):
    headers, _ = await register(client)
    created = await client.post("/api/goals", json={"title": "Crack placements", "description": "DSA + system design"}, headers=headers)
    assert created.status_code == 201
    goal = created.json()
    assert "career" in goal["relevant_categories"]
    listed = (await client.get("/api/goals", headers=headers)).json()
    assert [g["id"] for g in listed] == [goal["id"]]
    patched = await client.patch(f"/api/goals/{goal['id']}", json={"priority": 1, "status": "paused"}, headers=headers)
    assert patched.json()["status"] == "paused"
    assert (await client.delete(f"/api/goals/{goal['id']}", headers=headers)).status_code == 204
    assert (await client.get("/api/goals", headers=headers)).json() == []
    bad = await client.post("/api/goals", json={"title": "x", "starts_at": "2026-09-10T10:00:00+00:00",
                                                "ends_at": "2026-09-09T10:00:00+00:00"}, headers=headers)
    assert bad.status_code == 422


async def test_policy_compile_save_version_rollback_and_catalog(client):
    headers, _ = await register(client)
    await grant(client, headers)
    preview = await client.post("/api/policies/compile", json={"text": "I am preparing for placements. Block entertainment from 8-11 AM."},
                                headers=headers)
    assert preview.status_code == 200
    assert preview.json()["summary"]["focus_windows"] == ["08:00-11:00"]
    saved = await client.post("/api/policies", json={"text": "I am preparing for placements. Block entertainment from 8-11 AM."},
                              headers=headers)
    assert saved.status_code == 201, saved.text
    policy = saved.json()["policy"]
    assert policy["rules"][0]["rule_id"] == "r_block_entertainment" and saved.json()["goal_id"]
    rules = policy["rules"]
    rules[0]["escalation"] = "direct"
    updated = await client.put(f"/api/policies/{policy['id']}", json={"rules": rules}, headers=headers)
    assert updated.status_code == 200 and updated.json()["current_version"] == 2 and updated.json()["compiled_by"] == "manual"
    versions = (await client.get(f"/api/policies/{policy['id']}/versions", headers=headers)).json()
    assert [v["version"] for v in versions] == [2, 1]
    rolled = await client.post(f"/api/policies/{policy['id']}/rollback/1", headers=headers)
    assert rolled.json()["current_version"] == 3 and rolled.json()["rules"][0]["escalation"] == "soft_then_block"
    catalog = (await client.get("/api/policies/catalog")).json()
    assert any(a["essential"] for a in catalog["apps"])
    nothing = await client.post("/api/policies", json={"text": "lorem ipsum dolor", "allow_llm": False}, headers=headers)
    assert nothing.status_code == 422 and nothing.json()["error"]["code"] == "no_rules"


async def test_full_loop_events_evaluate_feedback_analytics_runs(client, clock):
    headers, _ = await register(client)
    await grant(client, headers)
    await client.post("/api/policies", json={"text": "Don't allow social media during study sessions."}, headers=headers)
    now = clock.now()
    batch = [ev("evt-focus-0001", "FOCUS_STARTED", now - timedelta(minutes=20), payload={"planned_minutes": 60}),
             ev("evt-open-00001", "APP_OPENED", now - timedelta(minutes=32), IG),
             ev("evt-ext-000001", "SESSION_EXTENDED", now, IG, {"duration_seconds": 1920, "scroll_events": 120}),
             ev("evt-bad-000001", "INTERVENTION_ACCEPTED", now, IG),
             {"client_event_id": "evt-bad-000002", "event_type": "APP_OPENED", "occurred_at": "not-a-date"},
             ev("evt-future-001", "APP_OPENED", now + timedelta(hours=2), IG)]
    ingest = await client.post("/api/events", json={"events": batch}, headers=headers)
    assert ingest.status_code == 200
    result = ingest.json()
    assert result["accepted"] == 3 and len(result["rejected"]) == 3
    dup = await client.post("/api/events", json={"events": batch[:1]}, headers=headers)
    assert dup.json()["duplicates"] == 1
    evaluation = await client.post("/api/interventions/evaluate", json={"app_package": IG}, headers=headers)
    assert evaluation.status_code == 200, evaluation.text
    body = evaluation.json()
    assert body["decision"] == "MINDFUL_PROMPT" and body["intervention_id"] and body["command"]["command"] == "MINDFUL_PROMPT"
    fb = await client.post("/api/interventions/feedback", json={"intervention_id": body["intervention_id"], "outcome": "accepted",
                                                               "satisfaction": 4}, headers=headers)
    assert fb.status_code == 200 and fb.json()["reward"] == 1.0
    again = await client.post("/api/interventions/feedback", json={"intervention_id": body["intervention_id"], "outcome": "overridden"},
                              headers=headers)
    assert again.status_code == 409
    listed = (await client.get("/api/interventions", headers=headers)).json()
    assert listed[0]["outcome"]["outcome"] == "accepted" and "ticket_nonce" not in listed[0]["command"]
    daily = (await client.get("/api/analytics/daily", headers=headers)).json()
    assert daily["social_minutes"] > 0 and 0 <= daily["attention_score"] <= 100 and daily["interventions"] >= 1
    assert daily["distraction_score"] is not None and daily["intervention_success_rate"] == 1.0
    trends = (await client.get("/api/analytics/trends?days=7", headers=headers)).json()
    assert len(trends["series"]) == 7 and trends["series"][-1]["social_minutes"] > 0
    weekly = (await client.get("/api/analytics/weekly", headers=headers)).json()
    assert len(weekly["days"]) == 7
    runs = (await client.get("/api/agent/runs", headers=headers)).json()
    assert {r["run_type"] for r in runs} >= {"evaluate", "feedback", "constitution"}
    detail = (await client.get(f"/api/agent/runs/{body['run_id']}", headers=headers)).json()
    assert [s["node"] for s in detail["steps"]] == body["path"] and detail["tool_calls"]
    assert isinstance(detail["llm_calls"], int) and isinstance(detail["model_calls"], list)
    status = (await client.get("/api/agent/status", headers=headers)).json()
    assert "decision" in status["graph"]["nodes"]
    assert len((await client.get("/api/agent/tools", headers=headers)).json()) == 13
    assert len((await client.get("/api/agent/prompts", headers=headers)).json()) == 8
    models = (await client.get("/api/models/status", headers=headers)).json()
    assert models["llm"]["provider"] == "mock" and models["embeddings"]["dim"] == 384
    recent = (await client.get("/api/usage/recent?hours=2", headers=headers)).json()
    assert recent["sessions"][0]["app_package"] == IG
    assert (await client.get("/api/risk/history", headers=headers)).json()
    dry = await client.post("/api/risk/assess", json={"app_package": IG}, headers=headers)
    assert dry.status_code == 200 and dry.json()["risk"]["band"] in {"low", "mild", "elevated", "high"}


async def test_overrides_settings_memory_knowledge_content(client):
    headers, _ = await register(client)
    await grant(client, headers)
    pause = await client.post("/api/overrides", json={"kind": "pause"}, headers=headers)
    assert pause.status_code == 201 and pause.json()["expires_at"] is None
    active = (await client.get("/api/overrides", headers=headers)).json()
    assert len(active) == 1
    assert (await client.delete(f"/api/overrides/{pause.json()['id']}", headers=headers)).json()["revoked_at"]
    settings = (await client.get("/api/settings", headers=headers)).json()
    assert {c["scope"] for c in settings["consents"] if c["granted"]} >= {"usage_monitoring"}
    bad_tz = await client.patch("/api/settings", json={"timezone": "Mars/Olympus"}, headers=headers)
    assert bad_tz.status_code == 422
    mem = await client.post("/api/memory", json={"content": "I focus best in the morning"}, headers=headers)
    assert mem.status_code == 201
    hits = (await client.post("/api/memory/search", json={"query": "morning focus"}, headers=headers)).json()
    assert hits[0]["content"] == "I focus best in the morning"
    assert (await client.delete(f"/api/memory/{mem.json()['id']}", headers=headers)).status_code == 204
    knowledge = (await client.get("/api/knowledge/search?q=display over other apps permission", headers=headers)).json()
    assert knowledge and knowledge[0]["slug"] == "android-capabilities"
    content = await client.post("/api/content/analyze", json={"text": "NPTEL lecture: thermodynamics derivation"}, headers=headers)
    assert content.status_code == 200 and content.json()["category"] == "education"


async def test_screenshot_requires_consent_and_strips_metadata(client):
    import io

    from PIL import Image

    headers, _ = await register(client)
    await grant(client, headers)
    buf = io.BytesIO()
    Image.new("RGB", (400, 800), "white").save(buf, format="PNG")
    denied = await client.post("/api/content/screenshot", files={"file": ("s.png", buf.getvalue(), "image/png")}, headers=headers)
    assert denied.status_code == 403
    await grant(client, headers, "screenshot_analysis")
    ok = await client.post("/api/content/screenshot", files={"file": ("s.png", buf.getvalue(), "image/png")}, headers=headers)
    assert ok.status_code == 200 and ok.json()["source"] == "screenshot"
    junk = await client.post("/api/content/screenshot", files={"file": ("s.png", b"not an image", "image/png")}, headers=headers)
    assert junk.status_code == 422


async def test_export_and_delete_data(client):
    headers, _ = await register(client)
    await grant(client, headers)
    await client.post("/api/goals", json={"title": "Thesis"}, headers=headers)
    export = await client.get("/api/user/export", headers=headers)
    assert export.status_code == 200 and "password_hash" not in export.text
    assert export.json()["tables"]["goals"]["rows"][0]["title"] == "Thesis"
    history = await client.delete("/api/user/data", headers=headers)
    assert history.status_code == 200 and history.json()["scope"] == "history"
    assert (await client.get("/api/goals", headers=headers)).json()  # goals survive history deletion
    refused = await client.delete("/api/user/data?scope=all", headers=headers)
    assert refused.status_code == 422
    gone = await client.delete("/api/user/data?scope=all&confirm=DELETE", headers=headers)
    assert gone.status_code == 200
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_admin_replay_dry_run_and_user_listing(client, clock):
    admin, _ = await register(client, "admin@example.com")
    user, _ = await register(client, "someone@synthetic.mindguard.dev")
    await grant(client, user)
    await client.post("/api/policies", json={"text": "Block Instagram strictly after 8 PM."}, headers=user)
    first = (await client.post("/api/interventions/evaluate", json={"app_package": IG, "session_minutes": 12}, headers=user)).json()
    assert first["decision"] == "TEMPORARY_BLOCK"
    assert (await client.post(f"/api/admin/runs/{first['run_id']}/replay", headers=user)).status_code == 403
    replay = await client.post(f"/api/admin/runs/{first['run_id']}/replay", headers=admin)
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["dry_run"] and body["original"]["final_decision"] == "TEMPORARY_BLOCK"
    assert "DUPLICATE_SUPPRESSED" in body["replay"]["guardrail_flags"]  # the recorded block is still active
    listed = (await client.get("/api/interventions", headers=user)).json()
    assert len(listed) == 1  # the replay was rolled back
    stats = (await client.get("/api/admin/stats?days=7", headers=admin)).json()
    assert set(stats["cost"]) >= {"projected_monthly_usd", "month_to_date_usd", "monthly_budget_usd", "budget_used_pct"}
    synthetic = (await client.get("/api/admin/users?synthetic=true", headers=admin)).json()
    assert [u["email"] for u in synthetic] == ["someone@synthetic.mindguard.dev"] and synthetic[0]["runs"] >= 1


async def test_settings_put_alias_matches_patch(client):
    headers, _ = await register(client, "put-alias@example.com")
    r = await client.put("/api/settings", json={"intervention_style": "strict"}, headers=headers)
    assert r.status_code == 200 and r.json()["intervention_style"] == "strict"
    r = await client.patch("/api/settings", json={"guardian_enabled": False}, headers=headers)
    assert r.json()["intervention_style"] == "strict" and r.json()["guardian_enabled"] is False
    assert (await client.put("/api/settings", json={"intervention_style": "chaotic"}, headers=headers)).status_code == 422
