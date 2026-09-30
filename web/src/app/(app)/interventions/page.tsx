"use client";

import { useState } from "react";
import { DecisionTag } from "@/components/DecisionTag";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import { OUTCOME_LABEL, appName, dateOf, reasonText, timeOf } from "@/lib/format";
import type { InterventionRecord, OutcomeType } from "@/lib/types";

const FEEDBACK: { outcome: OutcomeType; label: string }[] = [
  { outcome: "accepted", label: "It helped" },
  { outcome: "ignored", label: "I ignored it" },
  { outcome: "overridden", label: "I overrode it" },
];

export default function InterventionsPage() {
  const [pendingOnly, setPendingOnly] = useState(false);
  const list = useApi<InterventionRecord[]>("interventions?limit=100");
  const [notice, setNotice] = useState<string | null>(null);

  async function feedback(item: InterventionRecord, outcome: OutcomeType) {
    try {
      const res = await api.post<{ insights: string[] }>("interventions/feedback", { intervention_id: item.id, outcome });
      setNotice(res.insights[0] ?? "Thanks. MindGuard will use this to choose better next time.");
      list.reload();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Feedback could not be saved.");
    }
  }

  const items = (list.data ?? []).filter((i) => !pendingOnly || i.outcome === null);
  return (
    <div>
      <header className="page-head">
        <h1>AI interventions</h1>
        <p>Every action MindGuard took, why it took it, and how it went. Your feedback teaches it which kind of help works for you.</p>
      </header>
      <label className="check no-print"><input type="checkbox" checked={pendingOnly} onChange={(e) => setPendingOnly(e.target.checked)} />Only show ones waiting for feedback</label>
      {notice ? <p className="note note-growth" role="status" style={{ marginTop: "1rem" }}>{notice}</p> : null}
      <section className="section">
        {list.error ? <ErrorNote error={list.error} onRetry={list.reload} /> : !list.data ? <Loading /> : items.length === 0 ? (
          <Empty title={pendingOnly ? "Nothing waiting for feedback" : "No interventions yet"}>MindGuard only steps in when your rules or your current risk call for it.</Empty>
        ) : (
          <ol className="stack-lg" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {items.map((i) => (
              <li key={i.id} className="stack">
                <div className="row">
                  <DecisionTag decision={i.final_decision} />
                  <span className="soft small">{appName(i.app_package)} · {dateOf(i.created_at)} at {timeOf(i.created_at)}{i.duration_minutes ? ` · ${i.duration_minutes} min` : ""}</span>
                </div>
                <p>{i.explanation}</p>
                {i.command.body ? <p className="small soft">{i.command.body}</p> : null}
                <details>
                  <summary className="small">Why this decision</summary>
                  <ul className="small" style={{ marginTop: "0.4rem" }}>
                    {[...i.reason_codes, ...i.guardrail_flags].map((code) => <li key={code}>{reasonText(code)}</li>)}
                    {i.proposed_decision !== i.final_decision ? <li>The agent proposed “{i.proposed_decision.toLowerCase().replaceAll("_", " ")}”; safety rules changed it.</li> : null}
                    <li>Decided by: {i.decided_by === "llm" ? "AI reasoning, validated by rules" : i.decided_by === "bandit" ? "what has worked for you before" : "your rules and risk model"} ({Math.round(i.confidence * 100)}% confidence)</li>
                  </ul>
                </details>
                {i.outcome ? <p className="small">Outcome: <strong>{OUTCOME_LABEL[i.outcome.outcome]}</strong></p> : (
                  <div className="row no-print">{FEEDBACK.map((f) => <button key={f.outcome} type="button" className="btn btn-quiet" onClick={() => feedback(i, f.outcome)}>{f.label}</button>)}</div>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
