import uuid

PASSWORD = "correct horse 42 battery"


async def register(client, email=None, timezone="Asia/Kolkata"):
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "timezone": timezone})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body


async def grant(client, headers, *scopes):
    for scope in scopes or ("usage_monitoring", "content_text_analysis", "memory_personalization", "cloud_ai_reasoning"):
        r = await client.put(f"/api/settings/consents/{scope}", json={"granted": True}, headers=headers)
        assert r.status_code == 200, r.text
    r = await client.patch("/api/settings", json={"content_analysis_enabled": True}, headers=headers)
    assert r.status_code == 200, r.text
