"use client";

import { useEffect, useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { ApiError, api, useApi } from "@/lib/client";
import { dateOf } from "@/lib/format";
import type { ConstitutionPreview, Policy, PolicyVersion } from "@/lib/types";

const EXAMPLES = [
  "I am preparing for placements. Don't allow short videos during study sessions, but educational YouTube is fine.",
  "After 11 PM, block entertainment.",
  "If I exceed 45 minutes of social media, ask me before continuing.",
];

export default function ConstitutionPage() {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<ConstitutionPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const policies = useApi<Policy[]>("policies");
  const [versions, setVersions] = useState<Record<string, PolicyVersion[]>>({});

  useEffect(() => {
    const draft = new URLSearchParams(window.location.search).get("draft");
    if (draft) setText(draft.slice(0, 5000));
  }, []);

  async function run(kind: "preview" | "save") {
    setBusy(true); setError(null); setSaved(null);
    try {
      if (kind === "preview") {
        setPreview(await api.post<ConstitutionPreview>("policies/compile", { text }));
      } else {
        await api.post("policies", { text });
        setSaved("Saved. Your new rules apply from the next check on your phone.");
        setPreview(null);
        policies.reload();
      }
    } catch (e) {
      setError(e instanceof ApiError && e.code === "no_rules" ? "MindGuard found no rule it can enforce in that text. Name an app or content type, a time and an action." : e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function loadVersions(policy: Policy) {
    const list = await api.get<PolicyVersion[]>(`policies/${policy.id}/versions`);
    setVersions((v) => ({ ...v, [policy.id]: list }));
  }

  async function rollback(policy: Policy, version: number) {
    await api.post(`policies/${policy.id}/rollback/${version}`);
    policies.reload();
    await loadVersions(policy);
  }

  async function toggle(policy: Policy) {
    await api.patch(`policies/${policy.id}/status`, { status: policy.status === "active" ? "disabled" : "active" });
    policies.reload();
  }

  return (
    <div>
      <header className="page-head">
        <h1>Personal constitution</h1>
        <p>Write the rules you want in your own words. MindGuard shows exactly what it understood before anything is enforced.</p>
      </header>
      <section className="section stack">
        <label htmlFor="constitution">Your rules</label>
        <textarea id="constitution" value={text} maxLength={5000} onChange={(e) => setText(e.target.value)} placeholder={EXAMPLES[0]} />
        <div className="row small soft">Try:{EXAMPLES.map((ex) => <button key={ex} type="button" className="link-button" onClick={() => setText((t) => (t ? `${t} ${ex}` : ex))}>{ex.split(".")[0]}</button>)}</div>
        <div className="row">
          <button className="btn btn-quiet" type="button" disabled={busy || text.trim().length < 3} onClick={() => run("preview")}>Preview rules</button>
          <button className="btn" type="button" disabled={busy || !preview || preview.constitution.rules.length === 0} onClick={() => run("save")}>Save as my policy</button>
        </div>
        {error ? <p className="note note-alarm" role="alert">{error}</p> : null}
        {saved ? <p className="note note-growth" role="status">{saved}</p> : null}
      </section>

      {preview ? (
        <section className="section stack">
          <h2>What MindGuard understood</h2>
          {preview.constitution.goal ? <p>Goal: <strong>{preview.constitution.goal.title}</strong></p> : null}
          {preview.constitution.rules.length === 0 ? <Empty title="No enforceable rules found" /> : (
            <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {preview.constitution.rules.map((r) => (
                <li key={r.rule_id} className={`note ${r.effect === "ALLOW" ? "note-growth" : r.effect === "BLOCK" || r.effect === "LIMIT" ? "note-alarm" : "note-signal"}`}>
                  <p>{r.description}</p>
                  <p className="small soft">{r.effect === "ALLOW" ? "Exception" : `${r.effect.toLowerCase().replace("_", " ")}, escalation ${r.escalation.replaceAll("_", " ")}`}{r.max_duration_minutes ? `, up to ${r.max_duration_minutes} min` : ""}</p>
                </li>
              ))}
            </ul>
          )}
          {preview.constitution.unsupported.length > 0 ? (
            <div className="stack"><h3>Not possible</h3>{preview.constitution.unsupported.map((u, i) => <p key={i} className="note note-alarm small">“{u.text}”: {u.reason}</p>)}</div>
          ) : null}
          {preview.constitution.clarifications.length > 0 ? (
            <div className="stack"><h3>Please clarify</h3>{preview.constitution.clarifications.map((c, i) => <p key={i} className="note small">{c}</p>)}</div>
          ) : null}
          {preview.validation.issues.map((issue, i) => <p key={i} className={`note small ${issue.severity === "error" ? "note-alarm" : "note-signal"}`}>{issue.message}</p>)}
          <p className="small soft">Compiled by {preview.compiled_by === "llm" ? "AI, then validated" : "deterministic rules"}.</p>
        </section>
      ) : null}

      <section className="section stack">
        <h2>Active policies</h2>
        {policies.error ? <ErrorNote error={policies.error} onRetry={policies.reload} /> : !policies.data ? <Loading /> : policies.data.length === 0 ? (
          <Empty title="No policies yet">Write your first rules above.</Empty>
        ) : policies.data.map((p) => (
          <div key={p.id} className="surface stack">
            <div className="spread"><h3>{p.name}</h3><span className="small soft">Version {p.current_version} · {p.status}</span></div>
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>{p.rules.map((r) => <li key={r.rule_id}>{p.rule_explanations[r.rule_id] ?? r.description}</li>)}</ul>
            <div className="row small">
              <button type="button" className="link-button" onClick={() => toggle(p)}>{p.status === "active" ? "Turn off" : "Turn on"}</button>
              <button type="button" className="link-button" onClick={() => loadVersions(p)}>Version history</button>
            </div>
            {versions[p.id] ? (
              <table className="table"><thead><tr><th>Version</th><th>Created</th><th>By</th><th>Rules</th><th /></tr></thead>
                <tbody>{versions[p.id]!.map((v) => (
                  <tr key={v.version}><td className="num">{v.version}</td><td>{dateOf(v.created_at)}</td><td>{v.compiled_by}</td><td className="num">{v.rule_count}</td>
                    <td>{v.version !== p.current_version ? <button type="button" className="link-button" onClick={() => rollback(p, v.version)}>Restore</button> : "Current"}</td></tr>
                ))}</tbody></table>
            ) : null}
          </div>
        ))}
      </section>
    </div>
  );
}
