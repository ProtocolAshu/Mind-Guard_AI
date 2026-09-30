"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/client";
import type { InterventionStyle } from "@/lib/types";

const STEPS = ["Your goal", "Consent", "Your rules", "Style"] as const;

export default function OnboardingPage() {
  const [step, setStep] = useState(0);
  const [goal, setGoal] = useState("");
  const [consents, setConsents] = useState({ usage_monitoring: true, content_text_analysis: false, memory_personalization: true, cloud_ai_reasoning: false });
  const [rules, setRules] = useState("");
  const [style, setStyle] = useState<InterventionStyle>("balanced");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function next() {
    setBusy(true);
    setError(null);
    try {
      if (step === 0 && goal.trim()) await api.post("goals", { title: goal.trim() });
      if (step === 1) {
        for (const [scope, granted] of Object.entries(consents)) await api.put(`settings/consents/${scope}`, { granted });
        if (consents.content_text_analysis) await api.patch("settings", { content_analysis_enabled: true });
      }
      if (step === 2 && rules.trim()) await api.post("policies", { text: rules.trim() });
      if (step === 3) await api.patch("settings", { intervention_style: style });
      setStep((s) => s + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That step could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  if (step >= STEPS.length) {
    return (
      <div className="stack-lg">
        <h1>MindGuard is ready</h1>
        <p>Install the Android app and sign in with the same account to start protection on your phone. Everything can be changed later.</p>
        <Link className="btn" href="/dashboard">Go to today</Link>
      </div>
    );
  }
  return (
    <div>
      <header className="page-head"><h1>Set up MindGuard</h1></header>
      <ol className="steps">{STEPS.map((s, i) => <li key={s} aria-current={i === step ? "step" : undefined}>{i + 1}. {s}</li>)}</ol>
      <section className="section stack">
        {step === 0 ? (
          <div className="field"><label htmlFor="goal">What do you want your attention for?</label>
            <input id="goal" value={goal} maxLength={120} onChange={(e) => setGoal(e.target.value)} placeholder="I am preparing for placements" />
            <p className="small soft">Content that serves this goal is never treated as distraction.</p></div>
        ) : null}
        {step === 1 ? (
          <div className="stack">
            <p className="soft">Choose what MindGuard may use. Nothing is collected until you agree, and you can change this at any time.</p>
            {(Object.keys(consents) as (keyof typeof consents)[]).map((scope) => (
              <label key={scope} className="check"><input type="checkbox" checked={consents[scope]} onChange={(e) => setConsents({ ...consents, [scope]: e.target.checked })} />
                <span>{{ usage_monitoring: "App usage time (needed for any protection)", content_text_analysis: "Captions and titles, analysed on request",
                         memory_personalization: "Remember what helps me", cloud_ai_reasoning: "Cloud AI for unclear cases" }[scope]}</span></label>
            ))}
          </div>
        ) : null}
        {step === 2 ? (
          <div className="field"><label htmlFor="rules">Write your rules</label>
            <textarea id="rules" value={rules} onChange={(e) => setRules(e.target.value)} placeholder="Don't allow short videos during study sessions. After 11 PM, block entertainment." />
            <p className="small soft">You can review and refine them later on the Personal constitution page.</p></div>
        ) : null}
        {step === 3 ? (
          <fieldset style={{ border: 0, padding: 0 }}>
            <legend className="soft">How firm should MindGuard be?</legend>
            {(["gentle", "balanced", "strict"] as const).map((s) => (
              <label key={s} className="choice"><input type="radio" name="style" checked={style === s} onChange={() => setStyle(s)} />{s[0]!.toUpperCase() + s.slice(1)}</label>
            ))}
          </fieldset>
        ) : null}
        {error ? <p className="note note-alarm" role="alert">{error}</p> : null}
        <div className="row">
          <button type="button" className="btn" disabled={busy} onClick={next}>{step === STEPS.length - 1 ? "Finish setup" : "Continue"}</button>
          {step > 0 ? <button type="button" className="link-button" onClick={() => setStep(step - 1)}>Back</button> : null}
        </div>
      </section>
    </div>
  );
}
