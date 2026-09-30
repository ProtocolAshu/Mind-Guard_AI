"use client";

import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    const saved = window.localStorage.getItem("mg-theme");
    if (saved === "light" || saved === "dark") setTheme(saved);
  }, []);
  function apply(next: Theme) {
    setTheme(next);
    if (next === "system") {
      window.localStorage.removeItem("mg-theme");
      delete document.documentElement.dataset.theme;
    } else {
      window.localStorage.setItem("mg-theme", next);
      document.documentElement.dataset.theme = next;
    }
  }
  return (
    <label className="small">
      Theme
      <select value={theme} onChange={(e) => apply(e.target.value as Theme)} style={{ marginTop: "0.25rem" }}>
        <option value="system">Match device</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}
