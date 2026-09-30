// DTOs mirroring backend/app/schemas (kept in sync with the OpenAPI schema).

export type InterventionType =
  | "ALLOW" | "SOFT_WARNING" | "MINDFUL_PROMPT" | "DELAY" | "LIMITED_ACCESS" | "TEMPORARY_BLOCK" | "FOCUS_MODE" | "REQUEST_CONFIRMATION";
export type OutcomeType =
  | "accepted" | "overridden" | "ignored" | "stopped_session" | "continued_session" | "returned_to_task" | "disabled_protection";
export type InterventionStyle = "gentle" | "balanced" | "strict";
export type ConsentScope =
  | "usage_monitoring" | "content_text_analysis" | "cloud_ai_reasoning" | "screenshot_analysis" | "transcript_analysis"
  | "memory_personalization" | "anonymized_research";
export type GoalStatus = "active" | "paused" | "completed" | "archived";
export type OverrideKind = "allow_once" | "pause" | "disable_30m" | "disable_until_tomorrow" | "emergency";
export type PolicyEffect = "ALLOW" | "WARN" | "DELAY" | "REQUIRE_CONFIRMATION" | "LIMIT" | "BLOCK";
export type MemoryType = "semantic" | "episodic" | "preference" | "insight";
export type RunStatus = "running" | "succeeded" | "degraded" | "failed";

export interface User { id: string; email: string; display_name: string; role: "user" | "admin"; created_at: string }

export interface ApiErrorBody { error: { code: string; message: string; details: unknown; request_id: string | null } }

export interface TopApp { package: string; name: string; minutes: number }

export interface DailySummary {
  date: string;
  social_minutes: number;
  screen_minutes: number;
  productive_minutes: number;
  focus_minutes: number;
  sessions: number;
  long_sessions: number;
  opens: number;
  night_minutes: number;
  interventions: number;
  accepted: number;
  overridden: number;
  hourly_social_minutes: number[];
  top_apps: TopApp[];
  attention_score: number;
  interventions_by_type: Record<string, Record<string, number>>;
  intervention_success_rate: number | null;
  distraction_score: number | null;
  peak_distraction_score: number | null;
  doomscroll_score: number | null;
  risk_assessments: number;
  daily_limit_minutes: number;
}

export interface WeeklySummary {
  start: string;
  end: string;
  days: DailySummary[];
  totals: { social_minutes: number; focus_minutes: number; sessions: number; long_sessions: number; interventions: number; overrides: number };
  averages: { social_minutes_per_day: number; attention_score: number };
  change_vs_previous_week_pct: number | null;
  trigger_hours: number[];
  intervention_effectiveness: Record<string, { n: number; mean_reward: number }>;
  override_rate: number | null;
}

export interface TrendPoint {
  date: string; social_minutes: number; screen_minutes: number; productive_minutes: number; focus_minutes: number;
  long_sessions: number; interventions: number; attention_score: number;
}

export interface Insight {
  kind: string; source: string; headline: string; recommendation: string | null; evidence: string[];
  memory_id?: string; actionable?: boolean;
}

export interface InterventionRecord {
  id: string; run_id: string | null; app_package: string | null; proposed_decision: InterventionType; final_decision: InterventionType;
  duration_minutes: number; confidence: number; reason_codes: string[]; guardrail_flags: string[]; explanation: string;
  decided_by: string; status: string; created_at: string; expires_at: string | null;
  command: { body?: string; title?: string; command?: string };
  outcome: { outcome: OutcomeType; reward: number; satisfaction: number | null } | null;
}

export interface Goal {
  id: string; title: string; description: string; status: GoalStatus; priority: number; is_temporary: boolean;
  starts_at: string | null; ends_at: string | null; created_at: string; relevant_categories: string[];
}

export interface TimeWindow { start: string; end: string; days: number[] }
export interface RuleCondition {
  time_window: TimeWindow | null; focus_session: boolean | null; app_categories: string[]; app_packages: string[];
  content_categories: string[]; min_session_minutes: number | null; min_daily_minutes: number | null;
}
export interface PolicyRule {
  rule_id: string; description: string; effect: PolicyEffect; condition: RuleCondition; escalation: string;
  max_duration_minutes: number | null; priority: number; source_text: string;
}
export interface ValidationIssue { severity: "error" | "warning"; rule_id: string | null; message: string }
export interface ValidationReport { valid: boolean; issues: ValidationIssue[] }
export interface Policy {
  id: string; name: string; status: string; goal_id: string | null; current_version: number; compiled_by: string; source_text: string;
  rules: PolicyRule[]; rule_explanations: Record<string, string>; validation: ValidationReport; created_at: string; updated_at: string;
}
export interface PolicyVersion { version: number; compiled_by: string; rule_count: number; source_text: string; created_at: string }
export interface ConstitutionPreview {
  run_id: string;
  constitution: { goal: { title: string; description: string } | null; rules: PolicyRule[]; unsupported: { text: string; reason: string }[]; clarifications: string[] };
  compiled_by: string; validation: ValidationReport; parsed_sentences: string[]; unparsed_sentences: string[];
  summary: Record<string, unknown>; llm_used: boolean; grounding: string[];
}

export interface Override {
  id: string; kind: OverrideKind; app_package: string | null; reason: string | null; starts_at: string; expires_at: string | null;
  consumed_at: string | null; revoked_at: string | null;
}

export interface Memory {
  id: string; memory_type: MemoryType; content: string; importance: number; source: string; meta: Record<string, unknown>;
  created_at: string; expires_at: string | null;
}

export interface Preferences {
  intervention_style: InterventionStyle; timezone: string; guardian_enabled: boolean; ai_analysis_enabled: boolean;
  content_analysis_enabled: boolean; daily_social_limit_minutes: number | null; retention_days: number; theme: string;
  device_capabilities: Record<string, boolean | null>;
}
export interface Consent { scope: ConsentScope; granted: boolean; description: string; updated_at: string | null }
export interface Settings { preferences: Preferences; consents: Consent[] }

export interface RunSummary {
  id: string; user_id: string | null; run_type: string; status: RunStatus; final_decision: string | null; path: string[];
  error: string | null; started_at: string; latency_ms: number | null; llm_calls: number; tokens_in: number; tokens_out: number; cost_usd: number;
}
export interface RunDetail extends RunSummary {
  trigger: Record<string, unknown>;
  steps: { seq: number; node: string; status: string; latency_ms: number; input: unknown; output: unknown; error: string | null }[];
  tool_calls: { tool: string; status: string; latency_ms: number; arguments: unknown; result: unknown; error: string | null }[];
  model_calls: { provider: string; model: string; purpose: string; status: string; prompt_tokens: number; completion_tokens: number; latency_ms: number; cost_usd: number; cache_hit: boolean; error: string | null }[];
}
export interface AdminStats {
  users: number;
  runs: { run_type: string; status: string; count: number; avg_latency_ms: number }[];
  llm_usage: { provider: string; model: string; purpose: string; status: string; calls: number; prompt_tokens: number; completion_tokens: number; cost_usd: number; avg_latency_ms: number }[];
  decisions: Record<string, number>;
  guardrail_flags: Record<string, number>;
  tool_failures: { tool: string; status: string; count: number }[];
  cost: { window_days: number; window_spend_usd: number; projected_monthly_usd: number; month_to_date_usd: number;
          monthly_budget_usd: number; budget_used_pct: number | null; note: string };
}
export interface ReplayResult {
  original: { final_decision: string | null; path: string[]; status: string; started_at: string };
  replay: { decision: InterventionType; proposed: InterventionType; path: string[]; reason_codes: string[]; guardrail_flags: string[];
            explanation: string; explanation_points: string[]; degraded: boolean; notes: string[] };
  dry_run: boolean; content_replayed: boolean; decision_changed: boolean;
}
