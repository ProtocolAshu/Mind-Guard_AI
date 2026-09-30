"use client";

import { useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, useApi } from "@/lib/client";
import { dateOf } from "@/lib/format";
import type { Memory, MemoryType } from "@/lib/types";

const TYPE_LABEL: Record<MemoryType, string> = { semantic: "Things you told MindGuard", episodic: "Past moments", preference: "Learned preferences", insight: "Insights and suggestions" };

export default function MemoryPage() {
  const [type, setType] = useState<MemoryType | "">("");
  const list = useApi<Memory[]>(`memory${type ? `?type=${type}` : ""}`);
  const [note, setNote] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<{ content: string; similarity: number }[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post("memory", { content: note });
      setNote(""); setError(null); list.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the note.");
    }
  }

  async function search(e: React.FormEvent) {
    e.preventDefault();
    try {
      setHits(await api.post<{ content: string; similarity: number }[]>("memory/search", { query, k: 5 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed.");
    }
  }

  async function remove(m: Memory) {
    if (!window.confirm("Delete this memory permanently?")) return;
    await api.del(`memory/${m.id}`).catch(() => undefined);
    list.reload();
  }

  return (
    <div>
      <header className="page-head">
        <h1>Memory</h1>
        <p>What MindGuard remembers to personalise its decisions. It never stores the posts or videos you watched, only summaries like the ones below. Delete anything.</p>
      </header>
      {error ? <p className="note note-alarm" role="alert">{error}</p> : null}
      <section className="section columns">
        <form className="stack" onSubmit={add}>
          <h2>Tell MindGuard something</h2>
          <textarea value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="I focus best before noon" style={{ minHeight: "5rem" }} aria-label="Memory note" />
          <button className="btn" type="submit" disabled={!note.trim()}>Remember this</button>
        </form>
        <form className="stack" onSubmit={search}>
          <h2>Search memory</h2>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="late night instagram" aria-label="Search memory" />
          <button className="btn btn-quiet" type="submit" disabled={!query.trim()}>Search</button>
          {hits ? (hits.length === 0 ? <p className="soft small">No matching memories.</p> : (
            <ul className="small">{hits.map((h, i) => <li key={i}>{h.content} <span className="soft">({Math.round(h.similarity * 100)}% match)</span></li>)}</ul>
          )) : null}
        </form>
      </section>
      <section className="section stack">
        <div className="spread">
          <h2>Stored memories</h2>
          <select value={type} onChange={(e) => setType(e.target.value as MemoryType | "")} style={{ width: "auto" }} aria-label="Filter memories">
            <option value="">All kinds</option>
            {(Object.keys(TYPE_LABEL) as MemoryType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </select>
        </div>
        {list.error ? <ErrorNote error={list.error} onRetry={list.reload} /> : !list.data ? <Loading /> : list.data.length === 0 ? (
          <Empty title="Nothing remembered yet" />
        ) : (
          <table className="table"><thead><tr><th>Memory</th><th>Kind</th><th>Saved</th><th /></tr></thead>
            <tbody>{list.data.map((m) => (
              <tr key={m.id}><td>{m.content}</td><td>{TYPE_LABEL[m.memory_type]}</td><td>{dateOf(m.created_at)}</td>
                <td><button type="button" className="link-button" onClick={() => remove(m)}>Delete</button></td></tr>
            ))}</tbody></table>
        )}
      </section>
    </div>
  );
}
