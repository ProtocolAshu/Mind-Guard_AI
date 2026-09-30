"use client";

import { useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import { dateOf } from "@/lib/format";
import type { Goal, GoalStatus } from "@/lib/types";

export default function GoalsPage() {
  const goals = useApi<Goal[]>("goals");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("goals", { title, description, ends_at: endsOn ? new Date(`${endsOn}T23:59:00`).toISOString() : null });
      setTitle(""); setDescription(""); setEndsOn("");
      goals.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the goal.");
    }
  }

  async function update(goal: Goal, status: GoalStatus) {
    await api.patch(`goals/${goal.id}`, { status }).catch((err: unknown) => setError(err instanceof Error ? err.message : "Update failed."));
    goals.reload();
  }

  async function archive(goal: Goal) {
    await api.del(`goals/${goal.id}`).catch((err: unknown) => setError(err instanceof Error ? err.message : "Archive failed."));
    goals.reload();
  }

  return (
    <div>
      <header className="page-head">
        <h1>Goals</h1>
        <p>Goals decide which content counts as useful. Educational videos for an exam you are preparing for are never treated as distraction.</p>
      </header>
      <section className="section columns">
        <form className="stack" onSubmit={create}>
          <h2>Add a goal</h2>
          <div className="field"><label htmlFor="g-title">What are you working towards?</label>
            <input id="g-title" required maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Prepare for placements" /></div>
          <div className="field"><label htmlFor="g-desc">Details</label>
            <textarea id="g-desc" maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="DSA practice and system design" style={{ minHeight: "5rem" }} /></div>
          <div className="field"><label htmlFor="g-end">Ends on (optional)</label>
            <input id="g-end" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} /></div>
          {error ? <p className="note note-alarm" role="alert">{error}</p> : null}
          <button className="btn" type="submit">Add goal</button>
        </form>
        <div className="stack">
          <h2>Your goals</h2>
          {goals.error ? <ErrorNote error={goals.error} onRetry={goals.reload} /> : !goals.data ? <Loading /> : goals.data.length === 0 ? (
            <Empty title="No goals yet">Add one so MindGuard can tell useful content from distraction.</Empty>
          ) : (
            <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {goals.data.map((g) => (
                <li key={g.id} className="note">
                  <div className="spread"><strong>{g.title}</strong>
                    <select aria-label={`Status of ${g.title}`} value={g.status} onChange={(e) => update(g, e.target.value as GoalStatus)} style={{ width: "auto" }}>
                      <option value="active">Active</option><option value="paused">Paused</option><option value="completed">Completed</option>
                    </select>
                  </div>
                  {g.description ? <p className="small">{g.description}</p> : null}
                  <p className="small soft">Counts as useful: {g.relevant_categories.length ? g.relevant_categories.join(", ").replaceAll("_", " ") : "education"}
                    {g.ends_at ? ` · ends ${dateOf(g.ends_at)}` : ""}</p>
                  <button type="button" className="link-button small" onClick={() => archive(g)}>Archive</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
