"use client";

import { useEffect, useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import { appName, timeOf } from "@/lib/format";
import type { Override, OverrideKind } from "@/lib/types";

interface ActiveFocus { startedAt: string; minutes: number }
const SOCIAL = ["com.instagram.android", "com.google.android.youtube", "com.zhiliaoapp.musically", "com.snapchat.android", "com.twitter.android"];

function eventId(): string {
  return `web-${crypto.randomUUID()}`;
}

export default function FocusPage() {
  const overrides = useApi<Override[]>("overrides");
  const [focus, setFocus] = useState<ActiveFocus | null>(null);
  const [planned, setPlanned] = useState(50);
  const [app, setApp] = useState(SOCIAL[0]!);
  const [message, setMessage] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const saved = window.localStorage.getItem("mg-focus");
    if (saved) {
      const parsed = JSON.parse(saved) as ActiveFocus;
      if (Date.parse(parsed.startedAt) + parsed.minutes * 60_000 > Date.now()) setFocus(parsed);
      else window.localStorage.removeItem("mg-focus");
    }
    const t = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(t);
  }, []);

  async function startFocus() {
    const startedAt = new Date().toISOString();
    try {
      await api.post("events", { events: [{ client_event_id: eventId(), event_type: "FOCUS_STARTED", occurred_at: startedAt, payload: { planned_minutes: planned } }] });
      const next = { startedAt, minutes: planned };
      window.localStorage.setItem("mg-focus", JSON.stringify(next));
      setFocus(next);
      setMessage(`Focus session started for ${planned} minutes. Your study-session rules now apply.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not start focus mode.");
    }
  }

  async function endFocus() {
    await api.post("events", { events: [{ client_event_id: eventId(), event_type: "FOCUS_ENDED", occurred_at: new Date().toISOString(), payload: { completed: false } }] }).catch(() => undefined);
    window.localStorage.removeItem("mg-focus");
    setFocus(null);
    setMessage("Focus session ended.");
  }

  async function addOverride(kind: OverrideKind, appPackage: string | null) {
    if (kind === "emergency" && !window.confirm("Release every restriction now? You can resume protection at any time.")) return;
    try {
      await api.post("overrides", { kind, app_package: appPackage, reason: kind === "emergency" ? "emergency from dashboard" : null });
      setMessage(kind === "emergency" ? "Emergency override is on. Nothing is restricted." : "Done.");
      overrides.reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not change protection.");
    }
  }

  async function revoke(o: Override) {
    await api.del(`overrides/${o.id}`).catch(() => undefined);
    overrides.reload();
  }

  const remaining = focus ? Math.max(0, Math.round((Date.parse(focus.startedAt) + focus.minutes * 60_000 - now) / 60_000)) : 0;
  const kindLabel: Record<OverrideKind, string> = { allow_once: "Allowed once", pause: "Guardian paused", disable_30m: "Off for 30 minutes", disable_until_tomorrow: "Off until tomorrow", emergency: "Emergency override" };

  return (
    <div>
      <header className="page-head">
        <h1>Focus mode</h1>
        <p>Start a focus session to switch on your study-session rules, or take protection off when you genuinely need to.</p>
      </header>
      {message ? <p className="note" role="status">{message}</p> : null}
      <section className="section columns">
        <div className="stack">
          <h2>Focus session</h2>
          {focus ? (
            <>
              <p className="readout-value" style={{ fontSize: "var(--step-4)" }}>{remaining}<span className="readout-scale"> min left</span></p>
              <p className="soft">Started at {timeOf(focus.startedAt)}.</p>
              <button type="button" className="btn btn-quiet" onClick={endFocus}>End focus session</button>
            </>
          ) : (
            <>
              <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="small soft">Length</legend>
                {[25, 50, 90].map((m) => (
                  <label key={m} className="choice"><input type="radio" name="planned" checked={planned === m} onChange={() => setPlanned(m)} />{m} minutes</label>
                ))}
              </fieldset>
              <button type="button" className="btn" onClick={startFocus}>Start focus session</button>
              <p className="small soft">Your phone enforces the session; this dashboard records it so rules and analytics stay in sync.</p>
            </>
          )}
        </div>
        <div className="stack">
          <h2>Protection controls</h2>
          <div className="row">
            <button type="button" className="btn btn-quiet" onClick={() => addOverride("pause", null)}>Pause guardian</button>
            <button type="button" className="btn btn-quiet" onClick={() => addOverride("disable_30m", null)}>Off for 30 minutes</button>
          </div>
          <div className="field">
            <label htmlFor="once-app">Allow one app once</label>
            <div className="row">
              <select id="once-app" value={app} onChange={(e) => setApp(e.target.value)} style={{ width: "auto" }}>
                {SOCIAL.map((p) => <option key={p} value={p}>{appName(p)}</option>)}
              </select>
              <button type="button" className="btn btn-quiet" onClick={() => addOverride("allow_once", app)}>Allow once</button>
            </div>
          </div>
          <button type="button" className="btn btn-danger" onClick={() => addOverride("emergency", null)}>Emergency override</button>
          <p className="small soft">Calls, messages, maps, payments and alarms are never restricted, with or without an override.</p>
        </div>
      </section>
      <section className="section stack">
        <h2>Active overrides</h2>
        {overrides.error ? <ErrorNote error={overrides.error} onRetry={overrides.reload} /> : !overrides.data ? <Loading /> : overrides.data.length === 0 ? (
          <Empty title="Protection is running normally" />
        ) : (
          <table className="table"><thead><tr><th>Override</th><th>App</th><th>Since</th><th>Ends</th><th /></tr></thead>
            <tbody>{overrides.data.map((o) => (
              <tr key={o.id}><td>{kindLabel[o.kind]}</td><td>{o.app_package ? appName(o.app_package) : "All apps"}</td><td>{timeOf(o.starts_at)}</td>
                <td>{o.expires_at ? timeOf(o.expires_at) : "Until you resume"}</td>
                <td><button type="button" className="link-button" onClick={() => revoke(o)}>Resume protection</button></td></tr>
            ))}</tbody></table>
        )}
      </section>
    </div>
  );
}
