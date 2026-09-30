# Privacy

MindGuard is designed so that the smallest amount of data does the job, and so that a user can see and delete
everything it holds.

## What is collected

| Data | Why | Where |
|---|---|---|
| Which of *your chosen* apps is in the foreground and for how long | Detect sessions and risk | `events`, `sessions` |
| Focus sessions you start | Apply study-session rules | `events` |
| Goals and the rules you write | Decide what counts as distraction | `goals`, `policies` |
| Decisions, reasons and your responses | Explain, learn and improve | `interventions`, `intervention_outcomes` |
| Short summaries of what helps you | Personalisation | `memories` (consent-gated) |
| Captions, titles or screenshots **you share** | Classify content you asked about | analysed, **never stored** |

## What is never collected

Private messages, notification contents, keystrokes, continuous screen capture, accessibility snooping inside other
apps, location, contacts, or anything about other people. MindGuard does not monitor anyone but the account holder,
and does not run hidden: a persistent notification is visible whenever the guardian is on.

## Consent scopes

Seven independent scopes, all off until granted, each revocable at any time and recorded in the audit log:
`usage_monitoring`, `content_text_analysis`, `cloud_ai_reasoning`, `screenshot_analysis`, `transcript_analysis`,
`memory_personalization`, `anonymized_research`. Without `usage_monitoring` the system records nothing and never
intervenes; without `cloud_ai_reasoning` no content or context ever leaves the deployment for a third-party model.

## What reaches a cloud model

Only when the user granted `cloud_ai_reasoning` and only in two cases: an ambiguous decision (numeric risk features,
policy effect, app category, no raw content) and an uncertain content classification (sanitised text wrapped as
untrusted data). Content flagged as prompt injection is never sent. Screenshots are analysed in memory, stripped of
metadata, and discarded. Agent traces summarise inputs and redact secrets and raw text.

## User rights

- `GET /api/user/export` returns a single JSON file with every table row about the user (password hash excluded).
- `DELETE /api/user/data?scope=history` removes usage, interventions, risk scores, memories and learned preferences.
- `DELETE /api/user/data?scope=all&confirm=DELETE` deletes the account and all data.
- `retention_days` (default 90) bounds how long history is kept.
- Everything above is reachable from the dashboard's Privacy screen.

## Android permissions

Every permission, why it exists and what happens without it, is documented in [android.md](android.md#permissions).
The app requests no location, contacts, camera, microphone or `QUERY_ALL_PACKAGES` permission, uses a `queries`
declaration limited to the monitorable apps, and excludes its data from cloud backup and device transfer.
