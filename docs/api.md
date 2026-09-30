# API reference

Generated from the FastAPI OpenAPI schema by `scripts/generate_docs.py`; do not edit by hand.
70 operations on 60 paths. The machine-readable schema is
[`docs/api/openapi.json`](api/openapi.json); a running backend also serves Swagger UI at `/docs`.

Authentication: `Authorization: Bearer <access token>` from `POST /api/auth/login`. Access tokens last
`ACCESS_TOKEN_TTL_MINUTES`; refresh tokens rotate on every use and reuse revokes the whole token family.
Errors use one envelope: `{"error": {"code", "message", "details", "request_id"}}`.

## Admin

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/admin/audit/verify` | Audit Verify |
| `GET` | `/api/admin/runs` | Admin Runs |
| `GET` | `/api/admin/runs/{run_id}` | Admin Run |
| `POST` | `/api/admin/runs/{run_id}/replay` | Admin Replay |
| `GET` | `/api/admin/stats` | Admin Stats |
| `GET` | `/api/admin/users` | Admin Users |

## Agent status

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/agent/prompts` | Prompts |
| `GET` | `/api/agent/runs` | My Runs |
| `GET` | `/api/agent/runs/{run_id}` | My Run |
| `GET` | `/api/agent/status` | Agent Status |
| `GET` | `/api/agent/tools` | Tools |

## Analytics

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/analytics/daily` | Analytics Daily |
| `GET` | `/api/analytics/insights` | Analytics Insights |
| `GET` | `/api/analytics/trends` | Analytics Trends |
| `GET` | `/api/analytics/weekly` | Analytics Weekly |
| `GET` | `/api/knowledge/search` | Knowledge |
| `GET` | `/api/memory` | List Memory |
| `POST` | `/api/memory` | Add Memory |
| `POST` | `/api/memory/search` | Memory Search |
| `DELETE` | `/api/memory/{memory_id}` | Delete Memory |
| `DELETE` | `/api/user/data` | Delete Data |
| `GET` | `/api/user/export` | Export Data |

## Authentication

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/auth/login` | Login |
| `POST` | `/api/auth/logout` | Logout |
| `GET` | `/api/auth/me` | Me |
| `POST` | `/api/auth/refresh` | Refresh |
| `POST` | `/api/auth/register` | Register |

## Goals

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/goals` | Get Goals |
| `POST` | `/api/goals` | Create Goal |
| `DELETE` | `/api/goals/{goal_id}` | Archive Goal |
| `GET` | `/api/goals/{goal_id}` | Read Goal |
| `PATCH` | `/api/goals/{goal_id}` | Update Goal |

## Interventions

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/content/analyze` | Analyze Content |
| `POST` | `/api/content/screenshot` | Analyze Image |
| `GET` | `/api/interventions` | List Interventions |
| `POST` | `/api/interventions/evaluate` | Evaluate |
| `POST` | `/api/interventions/feedback` | Feedback |
| `GET` | `/api/interventions/{intervention_id}` | Read Intervention |
| `POST` | `/api/risk/assess` | Risk Assess |
| `GET` | `/api/risk/history` | Risk History |

## Model status

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/models/status` | Model Status |

## Policies

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/overrides` | Get Overrides |
| `POST` | `/api/overrides` | Add Override |
| `DELETE` | `/api/overrides/{override_id}` | Delete Override |
| `GET` | `/api/policies` | List Policies |
| `POST` | `/api/policies` | Create From Constitution |
| `GET` | `/api/policies/catalog` | Catalog |
| `POST` | `/api/policies/compile` | Compile Preview |
| `POST` | `/api/policies/suggestions/{memory_id}/accept` | Accept Suggestion |
| `POST` | `/api/policies/suggestions/{memory_id}/dismiss` | Dismiss Suggestion |
| `POST` | `/api/policies/validate` | Validate |
| `GET` | `/api/policies/{policy_id}` | Read Policy |
| `PUT` | `/api/policies/{policy_id}` | Replace Rules |
| `POST` | `/api/policies/{policy_id}/rollback/{version}` | Rollback |
| `PATCH` | `/api/policies/{policy_id}/status` | Change Status |
| `GET` | `/api/policies/{policy_id}/versions` | Versions |

## System

| Method | Path | Summary |
|---|---|---|
| `GET` | `/health` | Health |
| `GET` | `/ready` | Ready |

## Usage

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/events` | Post Events |
| `GET` | `/api/sessions` | List Sessions |
| `GET` | `/api/usage/recent` | Usage Recent |

## Users

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/settings` | Get Settings |
| `PATCH` | `/api/settings` | Update Settings |
| `PUT` | `/api/settings` | Update Settings |
| `GET` | `/api/settings/consents` | List Consents |
| `PUT` | `/api/settings/consents/{scope}` | Put Consent |
| `PUT` | `/api/settings/device` | Put Device |
| `GET` | `/api/users/me` | Get Me |
| `PATCH` | `/api/users/me` | Update Me |
| `POST` | `/api/users/me/password` | Change Password |

