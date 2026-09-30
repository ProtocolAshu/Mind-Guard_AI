"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import { dateOf, timeOf } from "@/lib/format";
import type { ReplayResult, RunDetail } from "@/lib/types";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const run = useApi<RunDetail>(`admin/runs/${id}`);
  const [replay, setReplay] = useState<ReplayResult | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  async function doReplay() {
    setReplayError(null);
    try {
      setReplay(await api.post<ReplayResult>(`admin/runs/${id}/replay`));
    } catch (e) {
      setReplayError(e instanceof Error ? e.message : "Replay failed.");
    }
  }

  if (run.error) return <ErrorNote error={run.error} onRetry={run.reload} />;
  if (!run.data) return <Loading label="Loading trace" />;
  const r = run.data;
  return (
    <div>
      <header className="page-head spread">
        <div>
          <h1>{r.run_type} run</h1>
          <p>{dateOf(r.started_at)} {timeOf(r.started_at)} · {r.status} · {r.latency_ms?.toFixed(0) ?? "—"} ms · {r.llm_calls} model calls · {r.tokens_in + r.tokens_out} tokens</p>
        </div>
        {r.run_type === "evaluate" || r.run_type === "replay" ? <button type="button" className="btn btn-quiet" onClick={doReplay}>Replay as dry run</button> : null}
      </header>
      {r.error ? <p className="note note-alarm">{r.error}</p> : null}
      {replayError ? <p className="note note-alarm" role="alert">{replayError}</p> : null}
      {replay ? (
        <section className="section stack">
          <h2>Replay</h2>
          <p>Original decision <strong>{replay.original.final_decision ?? "none"}</strong>; replay now decides <strong>{replay.replay.decision}</strong>
            {replay.decision_changed ? " (changed)" : " (unchanged)"}. Nothing was saved{replay.content_replayed ? "" : "; content text is never stored, so the replay used app metadata only"}.</p>
          <ul className="small">{replay.replay.explanation_points.map((p, i) => <li key={i}>{p}</li>)}</ul>
          {replay.replay.notes.length ? <pre className="json">{replay.replay.notes.join("\n")}</pre> : null}
        </section>
      ) : null}
      <section className="section stack">
        <h2>State transitions</h2>
        <ol className="timeline">
          {r.steps.map((step) => (
            <li key={step.seq}>
              <div className="spread"><strong>{step.node}</strong><span className="small soft num">{step.latency_ms.toFixed(1)} ms · {step.status}</span></div>
              {step.error ? <p className="small note note-alarm">{step.error}</p> : null}
              <details><summary className="small">State in and out</summary>
                <pre className="json">{JSON.stringify({ input: step.input, output: step.output }, null, 2)}</pre></details>
            </li>
          ))}
        </ol>
      </section>
      <section className="section scroll-x">
        <h2>Tool calls</h2>
        <table className="table">
          <thead><tr><th>Tool</th><th>Status</th><th>Latency</th><th>Arguments</th></tr></thead>
          <tbody>{r.tool_calls.map((t, i) => (
            <tr key={i}><td>{t.tool}</td><td>{t.status === "ok" ? "ok" : <span className="tag tag-alarm">{t.status}</span>}{t.error ? <div className="small soft">{t.error}</div> : null}</td>
              <td className="num">{t.latency_ms.toFixed(1)} ms</td>
              <td><details><summary className="small">Show</summary><pre className="json">{JSON.stringify(t.arguments, null, 2)}</pre></details></td></tr>
          ))}</tbody>
        </table>
      </section>
      <section className="section scroll-x">
        <h2>Model calls</h2>
        {r.model_calls.length === 0 ? <p className="soft">This run was decided without a language model.</p> : (
          <table className="table num">
            <thead><tr><th>Purpose</th><th>Model</th><th>Status</th><th>Tokens in/out</th><th>Latency</th><th>Cost</th></tr></thead>
            <tbody>{r.model_calls.map((c, i) => (
              <tr key={i}><td>{c.purpose}</td><td>{c.model}</td><td>{c.status}{c.cache_hit ? " (cached)" : ""}</td><td>{c.prompt_tokens}/{c.completion_tokens}</td>
                <td>{c.latency_ms.toFixed(0)} ms</td><td>${c.cost_usd.toFixed(5)}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </div>
  );
}
