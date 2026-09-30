"use client";

import Link from "next/link";
import { useState } from "react";
import type { ApiErrorBody } from "@/lib/types";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = mode === "login"
      ? { email, password }
      : { email, password, display_name: name, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" };
    try {
      const res = await fetch(`/api/session/${mode}`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), credentials: "same-origin",
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as ApiErrorBody | null;
        const requirements = (data?.error.details as { requirements?: string[] } | null)?.requirements;
        setError(requirements ? `Password needs ${requirements.join(", ")}.` : data?.error.message ?? "Something went wrong. Try again.");
        return;
      }
      const next = new URLSearchParams(window.location.search).get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;
      window.location.assign(mode === "register" ? "/onboarding" : safeNext ?? "/dashboard");
    } catch {
      setError("MindGuard could not be reached. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <form className="auth-panel stack-lg" onSubmit={submit} noValidate>
        <div>
          <h1>{mode === "login" ? "Sign in to MindGuard" : "Create your MindGuard account"}</h1>
          <p className="soft">{mode === "login" ? "See today's attention, your goals and what the guardian did." : "You decide what MindGuard watches. Nothing is monitored until you turn it on."}</p>
        </div>
        {mode === "register" ? (
          <div className="field"><label htmlFor="name">Name</label><input id="name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></div>
        ) : null}
        <div className="field"><label htmlFor="email">Email</label><input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete={mode === "login" ? "current-password" : "new-password"} />
          {mode === "register" ? <p className="small soft">At least 10 characters with letters and digits.</p> : null}
        </div>
        {error ? <p className="note note-alarm" role="alert">{error}</p> : null}
        <div className="row">
          <button className="btn" type="submit" disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</button>
          {mode === "login" ? <Link href="/register">Create an account</Link> : <Link href="/login">I already have an account</Link>}
        </div>
      </form>
    </div>
  );
}
