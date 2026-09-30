"use client";

import { Empty, ErrorNote, Loading } from "@/components/States";
import { useApi } from "@/lib/client";
import { DECISION_LABEL, dateOf, hourLabel } from "@/lib/format";
import type { Insight, InterventionType, WeeklySummary } from "@/lib/types";

export default function PatternsPage() {
  const weekly = useApi<WeeklySummary>("analytics/weekly");
  const insights = useApi<Insight[]>("analytics/insights");
  if (weekly.error) return <ErrorNote error={weekly.error} onRetry={weekly.reload} />;
  if (!weekly.data) return <Loading label="Finding patterns" />;
  const w = weekly.data;
  const max = Math.max(1, ...w.days.flatMap((d) => d.hourly_social_minutes));
  const patterns = (insights.data ?? []).filter((i) => i.kind === "pattern" || i.kind === "preference");
  const effectiveness = Object.entries(w.intervention_effectiveness).sort((a, b) => b[1].mean_reward - a[1].mean_reward);

  return (
    <div>
      <header className="page-head">
        <h1>Behavior patterns</h1>
        <p>When you reach for social media, and which kinds of help actually work for you.</p>
      </header>
      <section className="section scroll-x">
        <h2>Your week, hour by hour</h2>
        <div role="img" aria-label="Heat map of social-media minutes by day and hour"
             style={{ display: "grid", gridTemplateColumns: "72px repeat(24, minmax(14px, 1fr))", gap: 2, minWidth: 560 }}>
          <span />
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="small soft" style={{ textAlign: "center" }}>{h % 6 === 0 ? hourLabel(h) : ""}</span>)}
          {w.days.map((day) => [
            <span key={`${day.date}-l`} className="small">{dateOf(day.date)}</span>,
            ...day.hourly_social_minutes.map((m, h) => (
              <span key={`${day.date}-${h}`} title={`${dateOf(day.date)}, ${hourLabel(h)}: ${Math.round(m)} min`}
                    style={{ height: 18, borderRadius: 3, background: m <= 0 ? "var(--horizon-soft)" : `color-mix(in srgb, var(--horizon) ${Math.round(20 + (m / max) * 80)}%, transparent)` }} />
            )),
          ])}
        </div>
      </section>
      <section className="section columns">
        <div className="stack">
          <h2>What works for you</h2>
          {effectiveness.length === 0 ? <Empty title="No intervention outcomes this week" /> : (
            <table className="table num">
              <thead><tr><th>Intervention</th><th>Times</th><th>Average outcome</th></tr></thead>
              <tbody>
                {effectiveness.map(([type, e]) => (
                  <tr key={type}><td>{DECISION_LABEL[type as InterventionType] ?? type}</td><td>{e.n}</td><td>{e.mean_reward > 0 ? "+" : ""}{e.mean_reward.toFixed(2)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="small soft">Outcome runs from −1 (overridden) to +1 (it helped).</p>
        </div>
        <div className="stack">
          <h2>Learned patterns</h2>
          {patterns.length === 0 ? <Empty title="Nothing learned yet">Patterns appear once there is enough history.</Empty> : (
            <ul className="stack" style={{ padding: 0, listStyle: "none", margin: 0 }}>{patterns.map((p, i) => <li key={i} className="note">{p.headline}</li>)}</ul>
          )}
        </div>
      </section>
    </div>
  );
}
