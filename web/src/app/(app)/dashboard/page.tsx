"use client";

import Link from "next/link";
import { AttentionHorizon, BarList, Meter } from "@/components/Charts";
import { DecisionTag } from "@/components/DecisionTag";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { useApi } from "@/lib/client";
import { appName, hourRanges, minutes, percent, timeOf } from "@/lib/format";
import type { DailySummary, Insight, InterventionRecord, Settings, WeeklySummary } from "@/lib/types";

function currentHour(timeZone: string | undefined): number {
  const h = Number(new Date().toLocaleString("en-US", { hour: "numeric", hourCycle: "h23", timeZone }));
  return Number.isFinite(h) ? h % 24 : new Date().getHours();
}

export default function DashboardPage() {
  const daily = useApi<DailySummary>("analytics/daily");
  const weekly = useApi<WeeklySummary>("analytics/weekly");
  const insights = useApi<Insight[]>("analytics/insights");
  const recent = useApi<InterventionRecord[]>("interventions?limit=5");
  const settings = useApi<Settings>("settings");
  const tz = settings.data?.preferences.timezone;

  if (daily.error) return <ErrorNote error={daily.error} onRetry={daily.reload} />;
  if (!daily.data) return <Loading label="Reading today's attention" />;
  const d = daily.data;
  const triggers = weekly.data?.trigger_hours ?? [];
  const ranges = hourRanges(triggers);
  const top = insights.data?.find((i) => i.kind === "trigger_period") ?? insights.data?.[0];
  const draft = ranges.length > 0 && triggers.length > 0
    ? `Warn me about social media between ${String(triggers[0]).padStart(2, "0")}:00 and ${String(((triggers[triggers.length - 1] ?? 0) + 1) % 24).padStart(2, "0")}:00.`
    : null;
  const guardianOff = settings.data && !settings.data.preferences.guardian_enabled;
  const monitoring = settings.data?.consents.find((c) => c.scope === "usage_monitoring")?.granted;

  return (
    <div>
      <header className="page-head">
        <h1>Today</h1>
        <p>{minutes(d.social_minutes)} on social media of your {minutes(d.daily_limit_minutes)} daily limit.</p>
      </header>
      {guardianOff ? <p className="note note-signal">The guardian is off, so MindGuard is only observing. <Link href="/settings">Turn it on</Link></p> : null}
      {settings.data && monitoring === false ? <p className="note note-signal">Usage monitoring is not enabled yet. <Link href="/onboarding">Finish setup</Link></p> : null}

      <AttentionHorizon hourly={d.hourly_social_minutes} triggerHours={triggers} nowHour={settings.data ? currentHour(tz) : null} score={d.attention_score} />

      <section className="section columns">
        <div className="stack">
          <h2>Risk right now</h2>
          <Meter label="Distraction score" value={d.distraction_score}
                 caption={d.peak_distraction_score !== null ? `Peak today ${d.peak_distraction_score}, from ${d.risk_assessments} checks.` : "Appears after the first check from your phone."} />
          <Meter label="Doomscrolling score" value={d.doomscroll_score} />
        </div>
        <div className="stack">
          <h2>Today in numbers</h2>
          <table className="table num">
            <tbody>
              <tr><th scope="row">Screen time</th><td>{minutes(d.screen_minutes)}</td></tr>
              <tr><th scope="row">Social media</th><td>{minutes(d.social_minutes)}</td></tr>
              <tr><th scope="row">Productive time</th><td>{minutes(d.productive_minutes)}</td></tr>
              <tr><th scope="row">Focus time</th><td>{minutes(d.focus_minutes)}</td></tr>
              <tr><th scope="row">Interventions</th><td>{d.interventions}</td></tr>
              <tr><th scope="row">Intervention success</th><td>{percent(d.intervention_success_rate)}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>AI insight</h2>
        {top ? (
          <div className="stack">
            <p style={{ fontSize: "var(--step-1)" }}>{top.headline}</p>
            {top.recommendation ? <p className="soft">{top.recommendation}</p> : null}
            {draft ? <Link className="btn btn-quiet" href={`/constitution?draft=${encodeURIComponent(draft)}`}>Add this protection to my constitution</Link> : null}
          </div>
        ) : <Empty title="No patterns yet">Insights appear after a few days of usage data.</Empty>}
      </section>

      <section className="section columns">
        <div className="stack">
          <div className="spread"><h2>What MindGuard did</h2><Link href="/interventions" className="small">All interventions</Link></div>
          {recent.error ? <ErrorNote error={recent.error} /> : !recent.data ? <Loading /> : recent.data.length === 0 ? (
            <Empty title="No interventions today">MindGuard steps in only when your rules or your risk call for it.</Empty>
          ) : (
            <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {recent.data.map((i) => (
                <li key={i.id}>
                  <div className="row"><DecisionTag decision={i.final_decision} /><span className="small soft">{appName(i.app_package)} at {timeOf(i.created_at, tz)}</span></div>
                  <p className="small">{i.explanation}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="stack">
          <h2>Apps today</h2>
          {d.top_apps.length === 0 ? <Empty title="No app usage recorded today" /> : (
            <BarList items={d.top_apps.map((a) => ({ label: a.name, value: a.minutes, display: minutes(a.minutes) }))} />
          )}
          {ranges.length > 0 ? <p className="small soft">Your usual high-usage periods: {ranges.join(", ")}.</p> : null}
        </div>
      </section>
    </div>
  );
}
