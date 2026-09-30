# Agent architecture

Nine agents run as instrumented nodes in two LangGraph graphs (evaluation and feedback) plus a constitution graph.
Agents whose work must be reproducible and cheap (supervisor routing, context assembly, risk scoring) are
deterministic at runtime; their prompts remain the authoritative contract and are exposed read-only at
`GET /api/agent/prompts`.

| Agent | Node | Responsibility | Uses an LLM? |
|---|---|---|---|
| Supervisor | `supervisor` | Routes: short path for emergency override, paused guardian, essential app or missing consent; otherwise the full path | No |
| Context | `context` | Sessions, focus state, overrides, daily totals, timezone-local clock | No |
| Goal | `goal` | Active goals and what content they make relevant; compiles the constitution in its own graph | Only for sentences the deterministic compiler cannot parse |
| Content | `content` | Classifies caption/title/transcript/screenshot; sanitises and quarantines untrusted text | Only when local confidence < 0.55 and cloud consent exists |
| Behavior | `behavior` | 14-day profile: session lengths, long-session rate, accept/override rates, trigger hours | No |
| Policy | `policy` | Evaluates the compiled rule DSL, returns effect, winning rule, escalation | No |
| Risk | `risk` | 23 features → hybrid interpretable + calibrated GBM scores with factors | No |
| Decision | `decision` | Ladder recommendation, bandit choice, bounded LLM tool loop when ambiguous | Only inside the ambiguity band |
| Action | `action` | Builds the device command, writes the intervention, composes the explanation | No |
| Learning | `learning` (feedback graph) | Reward, bandit update, preference discovery, memory write, policy suggestion | Only for the reflection summary |

## Tools (section 23)

Thirteen typed tools; every call is schema-validated, permission-checked, recorded in `tool_calls` with latency and
status, and bounded in size. Device actions additionally require a signed ticket bound to user, run and app.

| Tool | Permission | LLM may call |
|---|---|---|
| `get_current_context`, `get_user_goals`, `get_user_policy`, `evaluate_policy` | read | no |
| `get_recent_usage`, `get_behavior_profile`, `retrieve_memories` | read | **yes** |
| `calculate_risk`, `classify_content` | compute | no |
| `request_user_confirmation`, `trigger_allowed_intervention` | device_action | no |
| `log_outcome`, `update_memory` | write | no |

The LLM may call only the three read-only tools, at most two tool turns, and can never write, intervene or change
policy. In the agent evaluation, all five LLM-requested tool calls left the deterministic decision unchanged.

## LLM architecture

- **Gateway** (`app/providers/gateway.py`): model tiering (fast vs reasoning), strict JSON schema validation,
  response cache, per-user daily token budget, monthly USD budget, timeout, bounded retries with jittered backoff,
  and a fallback model. Every call is recorded in `llm_calls` with tokens, latency, cost and status.
- **Prompts** (`app/agents/prompts.py`): each states role, objective, allowed inputs, output schema, forbidden
  behaviour, uncertainty handling and safety rules. Shared safety rules tell the model that `<untrusted_*>` blocks
  are data, that it cannot change policy or consent, and that its output is a proposal a deterministic engine will
  authorise.
- **Failure handling**: error, timeout, malformed JSON, schema violation and "compromised" output (a decision that
  tries to disable protection) all fall back to deterministic logic, flag `LLM_FALLBACK`, and mark the run degraded.
  Verified under fault injection: 104 runs across five LLM failure modes, 100% still returned a valid decision.

## Decision ladder

Risk band (0.45 / 0.60 / 0.75 thresholds) and policy effect produce a recommendation plus an eligible set:

- risk only: low → allow; mild → soft warning; elevated → mindful prompt, or a delay if already warned this session;
  high → delay, escalating to limited access or a temporary block inside a focus session.
- policy: `ALLOW` exception wins outright; `WARN`/`DELAY`/`REQUIRE_CONFIRMATION` map directly; `LIMIT`/`BLOCK` follow
  the rule's escalation strategy (`direct` enforces immediately, `adaptive` scales with risk, `soft_then_block`
  nudges first and enforces only after a warning was ignored).

## Guardrails

Fourteen ordered checks in `app/policies/guardrails.py`; each can soften or reject and each leaves a flag on the
intervention so the user sees why:

1. emergency override wins unconditionally · 2. guardian disabled or paused · 3. essential apps are never restricted ·
4. no usage-monitoring consent means no action · 5. "allow once" for this app · 6. low confidence never hard-blocks ·
7. hard actions need real risk unless the user's own rule demands them · 8. policy ceiling · 9. style ceiling ·
10. device capability (an overlay-less phone gets a notification) · 11. duration caps · 12. duplicate suppression and
cooldown · 13. hourly rate limit · 14. daily hard-intervention budget.

Defaults: minimum confidence 0.6 for hard actions, minimum risk 0.6, 10-minute cooldown, 6 interventions per hour,
8 hard interventions per day.

## Memory and RAG

- `memories` holds semantic, episodic, preference and insight rows with importance and expiry; embeddings live in
  `embeddings` with an HNSW cosine index (384 dimensions, offline hashing embedder by default).
- A four-document knowledge corpus (`app/rag/corpus/`) grounds the constitution compiler and explanations, including
  the Android capability document that keeps platform claims honest.
- Memory writes require `memory_personalization` consent. Preference memories are only written when a statistical
  test on outcomes supports them, not after a single event.

## Replay and debugging

`POST /api/admin/runs/{id}/replay` re-runs a recorded evaluation for its user inside a SAVEPOINT that is rolled back,
then reports the original decision, the new decision and whether it changed. Raw content is never stored, so replays
use app metadata only — a deliberate consequence of the privacy design.
