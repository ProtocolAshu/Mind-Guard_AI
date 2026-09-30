import type { InterventionType, OutcomeType } from "./types";

export function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const total = Math.round(value);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function hourLabel(hour: number): string {
  const h = ((hour % 24) + 24) % 24;
  if (h === 0) return "midnight";
  if (h === 12) return "noon";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

/** Contiguous trigger hours rendered as ranges, e.g. [22, 23, 0] -> "10 PM–1 AM". */
export function hourRanges(hours: readonly number[]): string[] {
  if (hours.length === 0) return [];
  const set = new Set(hours.map((h) => ((h % 24) + 24) % 24));
  const starts = [...set].filter((h) => !set.has((h + 23) % 24)).sort((a, b) => a - b);
  if (starts.length === 0) return ["all day"];
  return starts.map((start) => {
    let end = start;
    while (set.has((end + 1) % 24) && (end + 1) % 24 !== start) end = (end + 1) % 24;
    return `${hourLabel(start)}–${hourLabel(end + 1)}`;
  });
}

export type RiskBand = "low" | "mild" | "elevated" | "high";

/** Mirrors RiskAssessment.band in backend/app/schemas/agents.py (0.45 / 0.60 / 0.75). */
export const RISK_THRESHOLDS = { mild: 45, elevated: 60, high: 75 } as const;

export function riskBand(score0to100: number | null | undefined): RiskBand | null {
  if (score0to100 === null || score0to100 === undefined || !Number.isFinite(score0to100)) return null;
  if (score0to100 >= RISK_THRESHOLDS.high) return "high";
  if (score0to100 >= RISK_THRESHOLDS.elevated) return "elevated";
  if (score0to100 >= RISK_THRESHOLDS.mild) return "mild";
  return "low";
}

export const DECISION_LABEL: Record<InterventionType, string> = {
  ALLOW: "Allowed",
  SOFT_WARNING: "Heads-up",
  MINDFUL_PROMPT: "Mindful check-in",
  REQUEST_CONFIRMATION: "Asked to confirm",
  DELAY: "Short pause",
  LIMITED_ACCESS: "Limited access",
  TEMPORARY_BLOCK: "Paused app",
  FOCUS_MODE: "Focus suggested",
};

export const RESTRICTIVE: ReadonlySet<InterventionType> = new Set(["DELAY", "LIMITED_ACCESS", "TEMPORARY_BLOCK"]);

export const OUTCOME_LABEL: Record<OutcomeType, string> = {
  accepted: "Helped",
  overridden: "Overridden",
  ignored: "Ignored",
  stopped_session: "Stopped",
  continued_session: "Kept scrolling",
  returned_to_task: "Back to task",
  disabled_protection: "Protection turned off",
};

const REASON_TEXT: Record<string, string> = {
  FOCUS_SESSION_ACTIVE: "You were in a focus session",
  FOCUS_WINDOW_ACTIVE: "It was inside one of your protected windows",
  HIGH_RISK_SESSION: "Distraction risk was high",
  ELEVATED_RISK_SESSION: "Distraction risk was elevated",
  MILD_RISK_SESSION: "Distraction risk was mild",
  LOW_RISK_SESSION: "Distraction risk was low",
  LONG_SESSION: "The session had run long",
  ENTERTAINMENT_CONTENT: "The content looked like entertainment",
  GOAL_RELEVANT_CONTENT: "The content looked relevant to your goal",
  POLICY_RULE_MATCHED: "One of your rules applied",
  POLICY_EXCEPTION_ALLOWS: "One of your exceptions applied",
  DAILY_LIMIT_EXCEEDED: "You were over your daily limit",
  SESSION_LIMIT_EXCEEDED: "You were over your session limit",
  TRIGGER_HOUR: "It was one of your usual high-usage hours",
  NIGHT_USAGE: "It was late at night",
  REPEATED_OPENS: "You had opened social apps repeatedly",
  PERSONALIZED_CHOICE: "Chosen from what has worked for you before",
  MEMORY_PATTERN: "A gentler option has worked for you before",
  LOW_CONFIDENCE: "MindGuard was not certain",
  EMERGENCY_OVERRIDE: "Your emergency override was active",
  GUARDIAN_PAUSED: "You had paused the guardian",
  ALLOW_ONCE: "You chose Allow once",
  COOLDOWN: "A recent nudge was still cooling down",
  RATE_LIMITED: "Hourly nudge limit reached",
  DURATION_CAPPED: "Duration was capped by your settings",
  CAPABILITY_UNAVAILABLE: "Your device could not perform the stronger action",
  ESSENTIAL_APP_PROTECTED: "Essential apps are never restricted",
  UNTRUSTED_CONTENT_QUARANTINED: "Instructions hidden in the content were ignored",
  LLM_FALLBACK: "AI reasoning was unavailable; rules decided",
  DUPLICATE_SUPPRESSED: "A matching intervention was already active",
  BLOCK_BUDGET_EXHAUSTED: "Daily budget for restrictions was used up",
  STYLE_CEILING: "Softened to match your intervention style",
  POLICY_CEILING: "Kept within what your rule allows",
  CONSENT_MISSING: "Usage monitoring is not enabled",
  CONTENT_UNCERTAIN: "The content type was unclear",
  GUARDIAN_DISABLED: "The guardian was turned off",
  DOOMSCROLL_PATTERN: "It looked like a doomscrolling pattern",
  GOAL_CONFLICT: "It conflicted with an active goal",
};

export function reasonText(code: string): string {
  return REASON_TEXT[code] ?? code.toLowerCase().replaceAll("_", " ");
}

export function appName(pkg: string | null | undefined): string {
  if (!pkg) return "an app";
  const known: Record<string, string> = {
    "com.instagram.android": "Instagram", "com.google.android.youtube": "YouTube", "com.zhiliaoapp.musically": "TikTok",
    "com.snapchat.android": "Snapchat", "com.twitter.android": "X", "com.facebook.katana": "Facebook", "com.reddit.frontpage": "Reddit",
  };
  return known[pkg] ?? pkg.split(".").slice(-1)[0] ?? pkg;
}

export function timeOf(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone });
}

export function dateOf(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", timeZone });
}
