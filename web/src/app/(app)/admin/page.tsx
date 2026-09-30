"use client";

import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { useApi } from "@/lib/client";
import { dateOf, timeOf } from "@/lib/format";
import type { AdminStats, RunSummary } from "@/lib/types";

interface AdminUser { id: string; email: string; runs: number; synthetic: boolean; created_at: string }

export default function AdminPage() {
  const [status, setStatus] = useState("");
  const stats = useApi<AdminStats>("admin/stats?days=7");
  const runs = useApi<RunSummary[]>(`admin/runs?limit=100${status ? `&status=${status}` : ""}`);
  const synthetic = useApi<AdminUser[]>("admin/users?synthetic=true");
  const audit = useApi<{ valid: boolean; checked: number; reason: string | null }>("admin/audit/verify");

  if (stats.error) return <ErrorNote error={stats.error} onRetry={stats.reload} />;
  const s = stats.data;
  return (
    <div>
      <header className="page-head">
        <h1>Agent console</h1>
        <p>Runs, state transitions, tool calls, model latency, token use, failures and guardrail activity over the last 7 days.</p>
      </header>
      {!s ? <Loading /> : (
        <section className="section columns">
          <div className="stack">
            <h2>Runs</h2>
            <table className="table num"><thead><tr><th>Type</th><th>Status</th><th>Runs</th><th>Avg latency</th></tr></thead>
              <tbody>{s.runs.map((r) => <tr key={`${r.run_type}-${r.status}`}><td>{r.run_type}</td><td>{r.status}</td><td>{r.count}</td><td>{r.avg_latency_ms.toFixed(0)} ms</td></tr>)}</tbody></table>
            <p className="small soft">{s.users} users · audit chain {audit.data ? (audit.data.valid ? `verified (${audit.data.checked} entries)` : `BROKEN: ${audit.data.reason}`) : "checking"}</p>
          </div>
          <div className="stack">
            <h2>Model usage</h2>
            <p className="num">Month to date ${s.cost.month_to_date_usd.toFixed(2)} of ${s.cost.monthly_budget_usd.toFixed(2)} budget
              {s.cost.budget_used_pct !== null ? ` (${s.cost.budget_used_pct}%)` : ""}; projected ${s.cost.projected_monthly_usd.toFixed(2)} a month.</p>
            <p className="small soft">{s.cost.note}</p>
            {s.llm_usage.length === 0 ? <Empty title="No model calls" /> : (
              <table className="table num"><thead><tr><th>Model</th><th>Purpose</th><th>Status</th><th>Calls</th><th>Tokens</th><th>Latency</th></tr></thead>
                <tbody>{s.llm_usage.map((u, i) => <tr key={i}><td>{u.model}</td><td>{u.purpose}</td><td>{u.status}</td><td>{u.calls}</td><td>{u.prompt_tokens + u.completion_tokens}</td><td>{u.avg_latency_ms.toFixed(0)} ms</td></tr>)}</tbody></table>
            )}
          </div>
          <div className="stack">
            <h2>Decisions and guardrails</h2>
            <table className="table num"><tbody>{Object.entries(s.decisions).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table>
            <p className="small soft">Guardrail flags: {Object.entries(s.guardrail_flags).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(", ") || "none"}</p>
            {s.tool_failures.length > 0 ? <p className="note note-alarm small">Tool failures: {s.tool_failures.map((t) => `${t.tool} ${t.status} ×${t.count}`).join(", ")}</p> : null}
          </div>
        </section>
      )}
      <section className="section stack">
        <div className="spread">
          <h2>Recent runs</h2>
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "auto" }} aria-label="Filter runs by status">
            <option value="">All statuses</option><option value="succeeded">Succeeded</option><option value="degraded">Degraded</option><option value="failed">Failed</option>
          </select>
        </div>
        {runs.error ? <ErrorNote error={runs.error} onRetry={runs.reload} /> : !runs.data ? <Loading /> : runs.data.length === 0 ? <Empty title="No runs" /> : (
          <div className="scroll-x"><table className="table num">
            <thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Decision</th><th>Nodes</th><th>LLM calls</th><th>Latency</th></tr></thead>
            <tbody>{runs.data.map((r) => (
              <tr key={r.id}><td><Link href={`/admin/runs/${r.id}`}>{dateOf(r.started_at)} {timeOf(r.started_at)}</Link></td><td>{r.run_type}</td>
                <td>{r.status === "succeeded" ? r.status : <span className={`tag ${r.status === "failed" ? "tag-alarm" : "tag-signal"}`}>{r.status}</span>}</td>
                <td>{r.final_decision ?? "—"}</td><td>{r.path.length}</td><td>{r.llm_calls}</td><td>{r.latency_ms?.toFixed(0) ?? "—"} ms</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      <section className="section stack">
        <h2>Synthetic users</h2>
        <p className="soft small">Seeded from the simulator personas. Open any of their runs and replay it to debug decisions without changing their data.</p>
        {synthetic.data && synthetic.data.length > 0 ? (
          <ul className="small">{synthetic.data.map((u) => <li key={u.id}>{u.email} · {u.runs} runs</li>)}</ul>
        ) : <Empty title="No synthetic users">Run the seed script to create them.</Empty>}
      </section>
    </div>
  );
}
