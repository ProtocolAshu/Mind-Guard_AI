"use client";

import type { ApiError } from "@/lib/client";

export function Loading({ label = "Loading" }: { label?: string }) {
  return <p className="soft" aria-busy="true">{label}…</p>;
}

export function ErrorNote({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const hint = error.status === 0 || error.code === "api_unreachable"
    ? "The MindGuard API could not be reached. Start the backend and try again."
    : error.message;
  return (
    <div className="note note-alarm" role="alert">
      <p>{hint}</p>
      {onRetry ? <button type="button" className="link-button small" onClick={onRetry}>Try again</button> : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="note">
      <p><strong>{title}</strong></p>
      {children ? <div className="soft small">{children}</div> : null}
    </div>
  );
}
