"use client";

import { ErrorNote, Loading } from "@/components/States";
import { useApi } from "@/lib/client";
import { DECISION_LABEL, dateOf, hourRanges, minutes, percent } from "@/lib/format";
import type { InterventionType, WeeklySummary } from "@/lib/types";

export default function ReportsPage() {
  const weekly = useApi<WeeklySummary>("analytics/weekly");
  if (weekly.error) return <ErrorNote error={weekly.error} onRetry={weekly.reload} />;
  if (!weekly.data) return <Loading label="Preparing your weekly report" />;
  const w = weekly.data;
  return (
    <article>
      <header className="page-head spread">
        <div>
          <h1>Weekly report</h1>
          <p>{dateOf(w.start)} to {dateOf(w.end)}</p>
        </div>
        <button type="button" className="btn btn-quiet no-print" onClick={() => window.print()}>Print or save as PDF</button>
      </header>
      <section className="section columns">
        <table className="table num"><tbody>
          <tr><th scope="row">Social media</th><td>{minutes(w.totals.social_minutes)}</td></tr>
          <tr><th scope="row">Daily average</th><td>{minutes(w.averages.social_minutes_per_day)}</td></tr>
          <tr><th scope="row">Change from last week</th><td>{w.change_vs_previous_week_pct === null ? "No earlier week" : `${w.change_vs_previous_week_pct > 0 ? "+" : ""}${w.change_vs_previous_week_pct}%`}</td></tr>
          <tr><th scope="row">Average attention score</th><td>{w.averages.attention_score}</td></tr>
        </tbody></table>
        <table className="table num"><tbody>
          <tr><th scope="row">Focus time</th><td>{minutes(w.totals.focus_minutes)}</td></tr>
          <tr><th scope="row">Sessions</th><td>{w.totals.sessions}, of which {w.totals.long_sessions} ran 30 minutes or more</td></tr>
          <tr><th scope="row">Interventions</th><td>{w.totals.interventions}</td></tr>
          <tr><th scope="row">Overridden</th><td>{w.totals.overrides} ({percent(w.override_rate)})</td></tr>
        </tbody></table>
      </section>
      <section className="section">
        <h2>Trigger periods</h2>
        <p>{w.trigger_hours.length ? hourRanges(w.trigger_hours).join(", ") : "No distinct trigger periods this week."}</p>
      </section>
      <section className="section scroll-x">
        <h2>Intervention outcomes</h2>
        <table className="table num">
          <thead><tr><th>Intervention</th><th>Times</th><th>Average outcome</th></tr></thead>
          <tbody>{Object.entries(w.intervention_effectiveness).map(([k, v]) => (
            <tr key={k}><td>{DECISION_LABEL[k as InterventionType] ?? k}</td><td>{v.n}</td><td>{v.mean_reward.toFixed(2)}</td></tr>
          ))}</tbody>
        </table>
      </section>
      <section className="section scroll-x">
        <h2>Daily detail</h2>
        <table className="table num">
          <thead><tr><th>Day</th><th>Screen</th><th>Social</th><th>Productive</th><th>Focus</th><th>Night</th><th>Attention</th></tr></thead>
          <tbody>{w.days.map((d) => (
            <tr key={d.date}><td>{dateOf(d.date)}</td><td>{minutes(d.screen_minutes)}</td><td>{minutes(d.social_minutes)}</td><td>{minutes(d.productive_minutes)}</td>
              <td>{minutes(d.focus_minutes)}</td><td>{minutes(d.night_minutes)}</td><td>{d.attention_score}</td></tr>
          ))}</tbody>
        </table>
        <p className="small soft" style={{ marginTop: "0.75rem" }}>The attention score is a transparent heuristic, not a clinical measure. See the documentation for its formula.</p>
      </section>
    </article>
  );
}
