"use client";

import { useEffect, useState } from "react";
import { ErrorNote, Loading } from "@/components/States";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api, useApi } from "@/lib/client";
import type { InterventionStyle, Preferences, Settings } from "@/lib/types";

const STYLES: { value: InterventionStyle; label: string; detail: string }[] = [
  { value: "gentle", label: "Gentle", detail: "Heads-ups and check-ins; asks before anything stronger." },
  { value: "balanced", label: "Balanced", detail: "Nudges first, then short pauses or limits if you keep going." },
  { value: "strict", label: "Strict", detail: "Pauses apps when your rules say so." },
];

export default function SettingsPage() {
  const settings = useApi<Settings>("settings");
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { if (settings.data) setPrefs(settings.data.preferences); }, [settings.data]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!prefs) return;
    try {
      await api.patch("settings", {
        intervention_style: prefs.intervention_style, guardian_enabled: prefs.guardian_enabled, ai_analysis_enabled: prefs.ai_analysis_enabled,
        content_analysis_enabled: prefs.content_analysis_enabled, daily_social_limit_minutes: prefs.daily_social_limit_minutes,
        retention_days: prefs.retention_days, timezone: prefs.timezone,
      });
      setMessage("Settings saved.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not save settings.");
    }
  }

  if (settings.error) return <ErrorNote error={settings.error} onRetry={settings.reload} />;
  if (!prefs) return <Loading />;
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v });
  return (
    <form onSubmit={save}>
      <header className="page-head"><h1>Settings</h1><p>How firmly MindGuard steps in, and what it may analyse.</p></header>
      {message ? <p className="note" role="status">{message}</p> : null}
      <section className="section stack">
        <label className="check"><input type="checkbox" checked={prefs.guardian_enabled} onChange={(e) => set("guardian_enabled", e.target.checked)} />
          <span><strong>Guardian on</strong><br /><span className="small soft">When off, MindGuard records usage but never intervenes.</span></span></label>
      </section>
      <section className="section stack">
        <h2>Intervention style</h2>
        {STYLES.map((s) => (
          <label key={s.value} className="check"><input type="radio" name="style" checked={prefs.intervention_style === s.value} onChange={() => set("intervention_style", s.value)} />
            <span><strong>{s.label}</strong><br /><span className="small soft">{s.detail}</span></span></label>
        ))}
      </section>
      <section className="section columns">
        <div className="field"><label htmlFor="limit">Daily social-media limit (minutes)</label>
          <input id="limit" type="number" min={15} max={1440} value={prefs.daily_social_limit_minutes ?? ""} placeholder="120"
                 onChange={(e) => set("daily_social_limit_minutes", e.target.value ? Number(e.target.value) : null)} /></div>
        <div className="field"><label htmlFor="retention">Keep history for (days)</label>
          <input id="retention" type="number" min={7} max={365} value={prefs.retention_days} onChange={(e) => set("retention_days", Number(e.target.value))} /></div>
        <div className="field"><label htmlFor="tz">Time zone</label>
          <input id="tz" value={prefs.timezone} onChange={(e) => set("timezone", e.target.value)} /></div>
      </section>
      <section className="section stack">
        <h2>Analysis</h2>
        <label className="check"><input type="checkbox" checked={prefs.content_analysis_enabled} onChange={(e) => set("content_analysis_enabled", e.target.checked)} />
          <span>Analyse captions and titles on the device's request</span></label>
        <label className="check"><input type="checkbox" checked={prefs.ai_analysis_enabled} onChange={(e) => set("ai_analysis_enabled", e.target.checked)} />
          <span>Allow AI reasoning for unclear cases (also needs cloud AI consent on the Privacy page)</span></label>
      </section>
      <section className="section row"><button className="btn" type="submit">Save settings</button><div style={{ minWidth: 200 }}><ThemeToggle /></div></section>
    </form>
  );
}
