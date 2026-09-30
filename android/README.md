# MindGuard Android App — Scaffold Only

**This module is NOT compiled or runnable.** This sandbox has no Android
SDK, no Gradle distribution, and no access to Google's Maven repository
(`dl.google.com` is not on the allowed network list here) — so a real build
could not be verified even if the code were written. Per the master
prompt's "no fake implementations" rule, this is declared as a stub with a
concrete interface spec rather than presented as working Kotlin.

## What exists here
Module folder structure matching the target architecture
(`core/ data/ domain/ ai/ monitoring/ intervention/ permissions/ analytics/`).

## What each module must do (spec for the next engineering pass)

- **monitoring/**: Uses `UsageStatsManager` (requires the
  `PACKAGE_USAGE_STATS` special permission, granted via Settings, not a
  runtime permission dialog) to read app-foreground time. Emits
  `APP_OPENED`/`APP_CLOSED`/`SESSION_STARTED` events to the backend's
  `POST /api/events`. Does **not** read screen content by default — only
  package name + duration, per the privacy model in `docs/architecture.md`.
- **intervention/**: Renders the `Decision` returned by `/api/events` as an
  overlay/notification (`SOFT_WARNING`, `MINDFUL_PROMPT`) or, for
  `TEMPORARY_BLOCK`/`LIMITED_ACCESS`, uses Android's supported focus/DND
  APIs and app-pinning-style UI blocking — **never** an accessibility-service
  based hidden overlay, which Play Store policy restricts and the master
  prompt explicitly forbids ("no stealth monitoring or hidden device
  control").
- **permissions/**: Onboarding screens that explicitly request Usage Access
  and (optionally) notification access, explaining what is/is not
  collected, matching section 10's required onboarding flow.
- **ai/**: Thin HTTP client to the backend; does not embed any LLM or model
  on-device in this MVP (Phase 3 item).
- **data/ + domain/**: Local Room cache of goals/policies for offline
  operation, repository pattern.
- **analytics/**: Renders `GET /api/analytics/daily` in-app.

## Honest status
No `build.gradle`, no `AndroidManifest.xml`, no Kotlin source was written
here, because writing uncompiled, unverified Kotlin against a spec this
large would create exactly the "fake implementation that appears
functional" the master prompt prohibits. Building this for real needs an
Android Studio environment with SDK/Gradle/Google Maven access.
