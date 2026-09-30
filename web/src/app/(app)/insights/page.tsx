"use client";

import { useState } from "react";
import { BarList, Trend } from "@/components/Charts";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import { dateOf, hourRanges, minutes, percent } from "@/lib/format";
import type { Insight, TrendPoint, WeeklySummary } from "@/lib/types";

export default function InsightsPage() {
  const weekly = useApi<WeeklySummary>("analytics/weekly");
  const trends = useApi<{ series: TrendPoint[] }>("analytics/trends?days=30");
  const insights = useApi<Insight[]>("analytics/insights");
  const [message, setMessage] = useState<string | null>(null);

  async function decide(memoryId: string, action: "accept" | "dismiss") {
    try {
      await api.post(`policies/suggestions/${memoryId}/${action}`);
      setMessage(action === "accept" ? "Suggestion applied to your policy." : "Suggestion dismissed.");
      insights.reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not update the suggestion.");
    }
  }

  if (weekly.error) return <ErrorNote error={weekly.error} onRetry={weekly.reload} />;
  if (!weekly.data) return <Loading label="Summarising your week" />;
  const w = weekly.data;
  const series = trends.data?.series ?? [];
  const apps = new Map<string, number>();
  w.days.forEach((day) => day.top_apps.forEach((a) => apps.set(a.name, (apps.get(a.name) ?? 0) + a.minutes)));

  return (
    <div>
      <header className="page-head">
        <h1>Social-media insights</h1>
        <p>{dateOf(w.start)} to {dateOf(w.end)}: {minutes(w.totals.social_minutes)} in total, {minutes(w.averages.social_minutes_per_day)} a day
          {w.change_vs_previous_week_pct !== null ? `, ${w.change_vs_previous_week_pct > 0 ? "up" : "down"} ${Math.abs(w.change_vs_previous_week_pct)}% on last week` : ""}.</p>
      </header>
      {message ? <p className="note" role="status">{message}</p> : null}
      <section className="section columns">
        <Trend label="Social media per day, last 30 days" points={series.map((p) => p.social_minutes)} labels={series.map((p) => dateOf(p.date))} format={minutes} />
        <Trend label="Attention score, last 30 days" points={series.map((p) => p.attention_score)} labels={series.map((p) => dateOf(p.date))} format={(v) => `${Math.round(v)}/100`} />
      </section>
      <section className="section columns">
        <div className="stack">
          <h2>Top trigger periods</h2>
          {w.trigger_hours.length === 0 ? <Empty title="No clear trigger periods yet" /> : <p style={{ fontSize: "var(--step-1)" }}>{hourRanges(w.trigger_hours).join(", ")}</p>}
          <p className="soft small">Hours where your social-media use is well above your daily average.</p>
        </div>
        <div className="stack">
          <h2>Apps this week</h2>
          {apps.size === 0 ? <Empty title="No app usage recorded this week" /> : (
            <BarList items={[...apps.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([name, m]) => ({ label: name, value: m, display: minutes(m) }))} />
          )}
        </div>
      </section>
      <section className="section">
        <h2>What MindGuard noticed</h2>
        {insights.error ? <ErrorNote error={insights.error} /> : !insights.data ? <Loading /> : insights.data.length === 0 ? (
          <Empty title="Nothing to report yet">Insights need a few days of data.</Empty>
        ) : (
          <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {insights.data.map((i, idx) => (
              <li key={i.memory_id ?? `${i.kind}-${idx}`} className={`note ${i.actionable ? "note-signal" : ""}`}>
                <p>{i.headline}</p>
                {i.recommendation ? <p className="small soft">{i.recommendation}</p> : null}
                {i.actionable && i.memory_id ? (
                  <div className="row" style={{ marginTop: "0.5rem" }}>
                    <button type="button" className="btn" onClick={() => decide(i.memory_id!, "accept")}>Apply suggestion</button>
                    <button type="button" className="btn btn-quiet" onClick={() => decide(i.memory_id!, "dismiss")}>Dismiss</button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="section scroll-x">
        <h2>Day by day</h2>
        <table className="table num">
          <thead><tr><th>Day</th><th>Social</th><th>Productive</th><th>Focus</th><th>Long sessions</th><th>Interventions</th><th>Attention</th></tr></thead>
          <tbody>
            {w.days.map((day) => (
              <tr key={day.date}><td>{dateOf(day.date)}</td><td>{minutes(day.social_minutes)}</td><td>{minutes(day.productive_minutes)}</td>
                <td>{minutes(day.focus_minutes)}</td><td>{day.long_sessions}</td><td>{day.interventions}</td><td>{day.attention_score}</td></tr>
            ))}
          </tbody>
        </table>
        <p className="small soft" style={{ marginTop: "0.5rem" }}>Override rate this week: {percent(w.override_rate)}.</p>
      </section>
    </div>
  );
}
