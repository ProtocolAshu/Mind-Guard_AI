"use client";

import { ErrorNote, Loading } from "@/components/States";
import { useApi } from "@/lib/client";
import type { Settings } from "@/lib/types";

const CAPABILITIES: { key: string; title: string; enables: string; without: string }[] = [
  { key: "usage_access", title: "Usage access", enables: "Lets MindGuard see which app is open and for how long.", without: "MindGuard cannot notice sessions, so it never intervenes." },
  { key: "notifications", title: "Notifications", enables: "Heads-ups, mindful check-ins and confirmation prompts.", without: "Only on-screen pauses are possible, if allowed." },
  { key: "overlay", title: "Display over other apps", enables: "Pause screens, delays and limited-access gates on top of an app.", without: "Stronger interventions are softened to notifications." },
  { key: "dnd_access", title: "Do Not Disturb access", enables: "Silences notifications during focus sessions you start.", without: "Focus sessions still work, but notifications keep arriving." },
];

export default function PermissionsPage() {
  const settings = useApi<Settings>("settings");
  if (settings.error) return <ErrorNote error={settings.error} onRetry={settings.reload} />;
  if (!settings.data) return <Loading />;
  const caps = settings.data.preferences.device_capabilities;
  return (
    <div>
      <header className="page-head">
        <h1>Permissions</h1>
        <p>Android permissions are granted on your phone in the MindGuard app. This page shows what your phone last reported.</p>
      </header>
      <section className="section">
        <table className="table">
          <thead><tr><th>Permission</th><th>Status</th><th>What it enables</th><th>Without it</th></tr></thead>
          <tbody>{CAPABILITIES.map((c) => {
            const v = caps[c.key];
            return (
              <tr key={c.key}><td><strong>{c.title}</strong></td>
                <td>{v === true ? <span className="tag tag-growth">Granted</span> : v === false ? <span className="tag tag-alarm">Not granted</span> : <span className="tag">Not reported</span>}</td>
                <td>{c.enables}</td><td className="soft">{c.without}</td></tr>
            );
          })}</tbody>
        </table>
      </section>
      <section className="section stack">
        <h2>What MindGuard will never do</h2>
        <ul>
          <li>Run hidden or stealth monitoring: the app always shows a persistent notification while protecting you.</li>
          <li>Read private messages, or monitor anyone other than you.</li>
          <li>Restrict calls, messages, maps, payments or alarms.</li>
          <li>Force-close other apps. Android does not allow it; a pause screen or a return to the home screen is used instead.</li>
        </ul>
      </section>
    </div>
  );
}
