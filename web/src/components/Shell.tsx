"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useApi } from "@/lib/client";
import type { User } from "@/lib/types";
import { ThemeToggle } from "./ThemeToggle";

const GROUPS: { title: string; links: { href: string; label: string }[] }[] = [
  { title: "Attention", links: [{ href: "/dashboard", label: "Today" }, { href: "/insights", label: "Social-media insights" }, { href: "/patterns", label: "Behavior patterns" }, { href: "/reports", label: "Reports" }] },
  { title: "Goals", links: [{ href: "/goals", label: "Goals" }, { href: "/constitution", label: "Personal constitution" }, { href: "/focus", label: "Focus mode" }] },
  { title: "Risk", links: [{ href: "/interventions", label: "AI interventions" }, { href: "/memory", label: "Memory" }] },
  { title: "Control", links: [{ href: "/privacy", label: "Privacy" }, { href: "/permissions", label: "Permissions" }, { href: "/settings", label: "Settings" }] },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useApi<User>("auth/me");
  const groups = me.data?.role === "admin"
    ? [...GROUPS, { title: "Developer", links: [{ href: "/admin", label: "Agent console" }] }]
    : GROUPS;

  async function signOut() {
    await fetch("/api/session/logout", { method: "POST", credentials: "same-origin" });
    router.replace("/login");
  }

  return (
    <div className="shell">
      <aside className="rail">
        <Link href="/dashboard" className="brand">MindGuard<span>Guardian of your attention</span></Link>
        <nav className="nav" aria-label="Main">
          {groups.map((group) => (
            <div className="nav-group" key={group.title}>
              <p className="nav-title">{group.title}</p>
              {group.links.map((link) => (
                <Link key={link.href} href={link.href} className="nav-link"
                      aria-current={pathname === link.href || pathname.startsWith(`${link.href}/`) ? "page" : undefined}>
                  {link.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="rail-foot stack">
          {me.data ? <p className="small soft">{me.data.display_name || me.data.email}</p> : null}
          <ThemeToggle />
          <button type="button" className="link-button small" onClick={signOut}>Sign out</button>
        </div>
      </aside>
      <main className="main" id="content">{children}</main>
    </div>
  );
}
