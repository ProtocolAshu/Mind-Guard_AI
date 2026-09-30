"use client";

import { useState } from "react";
import { ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import type { Settings } from "@/lib/types";

const SCOPE_TITLE: Record<string, string> = {
  usage_monitoring: "App usage monitoring", content_text_analysis: "Caption and title analysis", cloud_ai_reasoning: "Cloud AI for unclear cases",
  screenshot_analysis: "Screenshots you share", transcript_analysis: "Video transcripts you share", memory_personalization: "Personal memory",
  anonymized_research: "Anonymised research statistics",
};

export default function PrivacyPage() {
  const settings = useApi<Settings>("settings");
  const [confirmText, setConfirmText] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function setConsent(scope: string, granted: boolean) {
    await api.put(`settings/consents/${scope}`, { granted }).catch((e: unknown) => setMessage(e instanceof Error ? e.message : "Update failed."));
    settings.reload();
  }

  async function deleteHistory() {
    if (!window.confirm("Delete all usage history, interventions, risk scores and memories? Goals, policies and your account stay.")) return;
    const res = await api.del<{ deleted: Record<string, number> }>("user/data?scope=history");
    const total = Object.values(res.deleted).reduce((a, b) => a + b, 0);
    setMessage(`Deleted ${total} records.`);
  }

  async function deleteAccount() {
    await api.del("user/data?scope=all&confirm=DELETE");
    await fetch("/api/session/logout", { method: "POST", credentials: "same-origin" });
    window.location.assign("/register");
  }

  if (settings.error) return <ErrorNote error={settings.error} onRetry={settings.reload} />;
  if (!settings.data) return <Loading />;
  return (
    <div>
      <header className="page-head">
        <h1>Privacy</h1>
        <p>Every kind of data MindGuard can use is off until you turn it on. Changes apply immediately and are recorded in the audit log.</p>
      </header>
      {message ? <p className="note" role="status">{message}</p> : null}
      <section className="section stack">
        <h2>What MindGuard may use</h2>
        {settings.data.consents.map((c) => (
          <label key={c.scope} className="check">
            <input type="checkbox" checked={c.granted} onChange={(e) => setConsent(c.scope, e.target.checked)} />
            <span><strong>{SCOPE_TITLE[c.scope] ?? c.scope}</strong><br /><span className="small soft">{c.description}</span></span>
          </label>
        ))}
      </section>
      <section className="section columns">
        <div className="stack">
          <h2>Download your data</h2>
          <p className="soft">A JSON file with everything stored about you: goals, policies, sessions, interventions, memories and agent traces.</p>
          <a className="btn btn-quiet" href="/api/mg/user/export" download>Download my data</a>
        </div>
        <div className="stack">
          <h2>Delete history</h2>
          <p className="soft">Removes usage history, interventions, risk scores, memories and learned preferences. Keeps your account, goals and policies.</p>
          <button type="button" className="btn btn-danger" onClick={deleteHistory}>Delete my history</button>
        </div>
      </section>
      <section className="section stack">
        <h2>Delete account</h2>
        <p className="soft">Permanently deletes your account and all data. Type DELETE to confirm.</p>
        <div className="row">
          <input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} aria-label="Type DELETE to confirm" style={{ maxWidth: 200 }} />
          <button type="button" className="btn btn-danger" disabled={confirmText !== "DELETE"} onClick={deleteAccount}>Delete account</button>
        </div>
      </section>
    </div>
  );
}
