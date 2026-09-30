"""Prometheus metrics (section 31). A dedicated registry keeps tests/app instances isolated
from the default global collector."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)
_LAT = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

HTTP_REQUESTS = Counter("mindguard_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram("mindguard_http_request_seconds", "HTTP latency", ["method", "route"], buckets=_LAT, registry=REGISTRY)
AGENT_RUNS = Counter("mindguard_agent_runs_total", "Agent runs", ["run_type", "status"], registry=REGISTRY)
AGENT_RUN_LATENCY = Histogram("mindguard_agent_run_seconds", "Agent run latency", ["run_type"], buckets=_LAT, registry=REGISTRY)
AGENT_STEP_LATENCY = Histogram("mindguard_agent_step_seconds", "Agent node latency", ["node", "status"], buckets=_LAT,
                               registry=REGISTRY)
LLM_CALLS = Counter("mindguard_llm_calls_total", "LLM calls", ["provider", "purpose", "status"], registry=REGISTRY)
LLM_TOKENS = Counter("mindguard_llm_tokens_total", "LLM tokens", ["model", "direction"], registry=REGISTRY)
LLM_COST = Counter("mindguard_llm_cost_usd_total", "Estimated LLM cost (USD)", ["model"], registry=REGISTRY)
LLM_LATENCY = Histogram("mindguard_llm_seconds", "LLM latency", ["provider", "purpose"], buckets=_LAT, registry=REGISTRY)
TOOL_CALLS = Counter("mindguard_tool_calls_total", "Tool calls", ["tool", "status"], registry=REGISTRY)
INTERVENTIONS = Counter("mindguard_interventions_total", "Authorized interventions", ["decision", "source"],
                        registry=REGISTRY)
GUARDRAIL_FLAGS = Counter("mindguard_guardrail_flags_total", "Guardrail flags raised", ["flag"], registry=REGISTRY)
EVENTS_INGESTED = Counter("mindguard_events_ingested_total", "Client events", ["event_type", "result"], registry=REGISTRY)
